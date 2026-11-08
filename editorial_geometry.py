#!/usr/bin/env python3
"""DSC09818 camera geometry anchor referenced to approved RAW PerspectiveLock V3.
Verified at width 2560 using 732/771 USAC_MAGSAC SIFT matches to V3.
NOT a universal transformation for other shots. Receding balcony rails stay convergent.
"""
import argparse, hashlib, json, pathlib, sys
import cv2
import numpy as np

FRAME="DSC09818"
PROFILE="DSC09818-RAW-V3-v1"
W0=2560
M0=np.array([[1.04663131,-0.0875458321,-19.4500589],[0.000391355505,1.01807588,-39.1088827],[0.000000409089074,-0.0000324583254,1.0]],dtype=np.float64)

def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
 return h.hexdigest()

def read(path):
 im=cv2.imread(str(path),cv2.IMREAD_COLOR)
 if im is None:raise ValueError("undecodable:"+str(path))
 return im

def verticals(im):
 h,w=im.shape[:2];small=cv2.resize(im,(1200,round(1200*h/w)),interpolation=cv2.INTER_AREA);h,w=small.shape[:2]
 roi=small[int(h*.08):int(h*.93),int(w*.04):int(w*.47)]
 lab=cv2.createCLAHE(2.0,(8,8)).apply(cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY))
 lines=cv2.HoughLinesP(cv2.Canny(lab,50,130),1,np.pi/1800,50,minLineLength=95,maxLineGap=35)
 sl=[]
 if lines is not None:
  for x1,y1,x2,y2 in lines.reshape(-1,4):
   dy=y2-y1;dx=x2-x1
   if abs(dy)>95 and abs(dx/dy)<.25:sl.append(float(dx/dy))
 return {"count":len(sl),"median_slope":float(np.median(sl)) if sl else None,"median_abs":float(np.median(np.abs(sl))) if sl else None}

def prepare(src_path,out_path,report_path):
 src=read(src_path);h,w=src.shape[:2]
 S=np.diag([w/W0,w/W0,1.0]);H=S@M0@np.linalg.inv(S)
 lock=cv2.warpPerspective(src,H,(w,h),flags=cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_CONSTANT)
 mask=cv2.warpPerspective(np.full((h,w),255,np.uint8),H,(w,h),flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT)
 before,after=verticals(src),verticals(lock);valid=float(np.mean(mask==255))
 ok=(valid>.997 and before["count"]>=12 and after["count"]>=12 and after["median_slope"] is not None and abs(after["median_slope"])<.015)
 if not ok:raise RuntimeError("GEOMETRY_HOLD:"+json.dumps({"before":before,"after":after,"valid":valid}))
 if not cv2.imwrite(out_path,lock,[cv2.IMWRITE_JPEG_QUALITY,95]):raise RuntimeError("write_failed")
 obj={"project":"tropicana-park","frame":FRAME,"profile":PROFILE,"geometry_status":"PASS","reference":"approved RAW PerspectiveLock V3","reference_inliers":"732/771 SIFT+USAC_MAGSAC","source_sha256":sha(src_path),"result_sha256":sha(out_path),"source_dimensions":[w,h],"output_dimensions":[w,h],"homography":H.tolist(),"valid_fraction":valid,"before":before,"after":after,"lens":"lens-profile correction omitted because verified profile unavailable","rails":"preserve natural perspective convergence"}
 pathlib.Path(report_path).write_text(json.dumps(obj,ensure_ascii=False,indent=2))
 print(json.dumps(obj,ensure_ascii=False))

def compare(reference,candidate,out_path):
 ref=read(reference);cand=read(candidate)
 h,w=ref.shape[:2];rw=1536;rh=int(round(h*rw/w))
 A=cv2.resize(ref,(rw,rh));B=cv2.resize(cand,(rw,rh))
 clahe=cv2.createCLAHE(2,(8,8))
 sift=cv2.SIFT_create(nfeatures=4000)
 kp1,d1=sift.detectAndCompute(clahe.apply(cv2.cvtColor(A,cv2.COLOR_BGR2GRAY)),None)
 kp2,d2=sift.detectAndCompute(clahe.apply(cv2.cvtColor(B,cv2.COLOR_BGR2GRAY)),None)
 if d1 is None or d2 is None:raise RuntimeError("GEOMETRY_HOLD:no_keypoints")
 pairs=cv2.BFMatcher().knnMatch(d2,d1,k=2)
 good=[m for m,n in pairs if m.distance<.72*n.distance]
 if len(good)<30:raise RuntimeError("GEOMETRY_HOLD:low_keypoint_overlap")
 src=np.float32([kp2[m.queryIdx].pt for m in good]);dst=np.float32([kp1[m.trainIdx].pt for m in good])
 H,mask=cv2.findHomography(src,dst,cv2.USAC_MAGSAC,3.0)
 if H is None:raise RuntimeError("GEOMETRY_HOLD:unmatched_content")
 n=int(mask.sum());ratio=n/len(good)
 corners=np.float32([[[0,0]],[[rw,0]],[[rw,rh]],[[0,rh]]])
 drift=np.linalg.norm(cv2.perspectiveTransform(corners,H).reshape(-1,2)-corners.reshape(-1,2),axis=1)
 maxdrift=float(drift.max()/np.hypot(rw,rh))
 vert=verticals(cand)
 status="PASS" if n>=60 and ratio>=.5 and maxdrift<.012 and vert["median_slope"] is not None and abs(vert["median_slope"])<.02 else "HOLD"
 obj={"project":"tropicana-park","frame":FRAME,"source_sha256":sha(reference),"candidate_sha256":sha(candidate),"status":status,"matches":len(good),"inliers":n,"inlier_ratio":ratio,"max_corner_drift_fraction":maxdrift,"candidate_verticals":vert,"source_verticals":verticals(ref),"scope":"geometry only; manual scene fidelity required"}
 pathlib.Path(out_path).write_text(json.dumps(obj,ensure_ascii=False,indent=2))
 print(json.dumps(obj,ensure_ascii=False))
 return 0 if status=="PASS" else 2

if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("command",choices=["prepare","compare"]);p.add_argument("--source");p.add_argument("--output");p.add_argument("--report",required=True);p.add_argument("--reference");p.add_argument("--candidate");a=p.parse_args()
 if a.command=="prepare":prepare(a.source,a.output,a.report)
 else:sys.exit(compare(a.reference,a.candidate,a.report))
