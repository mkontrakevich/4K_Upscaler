#!/usr/bin/env python3
"""DSC09818 technical RAW master. One real 1/160s ISO3200 RAW; no generation or fake bracket HDR."""
import gc,hashlib,json,os
from pathlib import Path
import cv2,numpy as np,tifffile
cv2.setNumThreads(2)
B=Path(os.environ.get('TROPICANA_TECH_DIR','/tmp/tropicana-tech'));B.mkdir(parents=True,exist_ok=True)
RAW=B/'DSC09818.ARW';BASE=B/'DSC09818_linear16.tif';LINEAR=B/'DSC09818_TECH_LINEAR16.tif';TEMP=B/'DSC09818_TECH_VIRT_EV16_TEMP.tif';CLEAN=B/'DSC09818_TECH_CLEAN16.tif'
J=B/'DSC09818_TECH_QA.json';ORIG_SHA='1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5'
H0=np.array([[1.04663131,-.0875458321,-19.4500589],[.000391355505,1.01807588,-39.1088827],[.000000409089074,-.0000324583254,1.]],np.float64)
ABC=(.021,-.052,-.003);W,H=6536,4360;WT=np.array([.2126,.7152,.0722],np.float32)
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def write(path,arr,label):
 tifffile.imwrite(path,arr,photometric='rgb',compression='zlib',compressionargs={'level':3},rowsperstrip=128,bigtiff=True,metadata={'description':label,'source_raw_sha256':ORIG_SHA})
def jpeg(path,arr):
 sm=cv2.resize(arr,(2000,round(H*2000/W)),interpolation=cv2.INTER_AREA)
 sm=np.uint8(np.clip(np.round(sm.astype(np.float32)/257.),0,255))
 cv2.imwrite(str(path),sm[:,:,::-1],[cv2.IMWRITE_JPEG_QUALITY,94])
def raw():
 assert sha(RAW)==ORIG_SHA,'source SHA invalid'
 with tifffile.TiffFile(RAW) as t:
  tags=t.pages[0].tags;ex=tags['ExifTag'].value
  meta=dict(camera=tags['Model'].value,lens=ex['LensModel'],focal=ex['FocalLength'][0]/ex['FocalLength'][1],aperture=ex['FNumber'][0]/ex['FNumber'][1],shutter=ex['ExposureTime'][0]/ex['ExposureTime'][1],iso=ex['ISOSpeedRatings'])
 assert meta==dict(camera='ILCE-7M4',lens='FE 16-35mm F2.8 GM',focal=16.,aperture=2.8,shutter=.00625,iso=3200),meta
 if not BASE.exists():
  import rawpy
  with rawpy.imread(str(RAW)) as r:
   im=r.postprocess(use_camera_wb=True,no_auto_bright=True,output_bps=16,gamma=(1,1),highlight_mode=rawpy.HighlightMode.Blend,output_color=rawpy.ColorSpace.sRGB)
  write(BASE,im,'16bit linear sRGB from original SONY ARW, camera WB')
  del im
 return meta
def geometry():
 img=tifffile.imread(BASE);assert img.shape==(4688,7028,3) and img.dtype==np.uint16,img.shape
 h,w=img.shape[:2];S=np.diag([w/2560,w/2560,1]);inv=np.linalg.inv(S@H0@np.linalg.inv(S))
 dx=(w-W)//2;dy=(h-H)//2;a,b,c=ABC;cx=(w-1)/2;cy=(h-1)/2;radius=min(w,h)/2
 dest=np.memmap(B/'geometry.mmap',mode='w+',dtype=np.uint16,shape=(H,W,3));valid=0;total=0;maxshift=0
 for y0 in range(0,H,144):
  y1=min(H,y0+144);yy,xx=np.mgrid[y0:y1,0:W].astype(np.float32);xx+=dx;yy+=dy
  d=inv[2,0]*xx+inv[2,1]*yy+inv[2,2];px=(inv[0,0]*xx+inv[0,1]*yy+inv[0,2])/d;py=(inv[1,0]*xx+inv[1,1]*yy+inv[1,2])/d
  nr=np.hypot((px-cx)/radius,(py-cy)/radius);f=1-a-b-c+c*nr+b*nr*nr+a*nr*nr*nr
  mx=cx+(px-cx)*f;my=cy+(py-cy)*f;good=(mx>=0)&(mx<w-1)&(my>=0)&(my<h-1)
  valid+=int(good.sum());total+=mx.size;maxshift=max(maxshift,float(np.max(np.hypot(mx-px,my-py))))
  dest[y0:y1]=cv2.remap(img,mx.astype(np.float32),my.astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
 write(LINEAR,dest,'16bit scene-linear genuine ARW, PTLens 16mm third-party and PerspectiveLock V3 in ONE resample, no tone or noise reduction')
 del img,dest;gc.collect();os.remove(B/'geometry.mmap')
 return dict(source_sha256=sha(RAW),optical_abc=list(ABC),lens_model='third_party_PTLens_at_16mm_not_Sony_official',perspective_matrix=H0.tolist(),crop_xywh=[dx,dy,W,H],valid_fraction=valid/total,max_optical_displacement_px=maxshift,geometry_process='single resample of real RAW-developed linear RGB',linear16_sha256=sha(LINEAR))
def smooth(z,a,b):
 z=np.clip((z-a)/(b-a),0,1);return z*z*(3-2*z)
def tone(rgb,mode,y0=0):
 v=rgb.astype(np.float32)/65535
 L=v@WT;signal=np.maximum(L-.000105,0)
 def render(E):
  t=(signal*E/(signal*E+.195))**.86
  raw=np.clip(v*(t/np.maximum(L,.0001))[:,:,None],0,1)
  return raw,raw@WT
 neutral,yn=render(32)
 if mode=='neutral':return np.uint16(np.round(np.clip(neutral,0,1)*65535))
 detail=cv2.GaussianBlur(L,(0,0),2.8)
 conf=smooth(detail,.00018,.0012)
 xs=(np.arange(W,dtype=np.float32)/W)[None,:]
 ys=(np.arange(y0,y0+L.shape[0],dtype=np.float32)/H)[:,None]
 # City and bay occupancy is measured on this exact RAW crop; no invented scene geometry.
 city=smooth(xs,.43,.61)*(1-smooth(ys,.72,.82))*smooth(ys,.29,.40)
 exposure=32+21*conf*(1-smooth(signal,.018,.085))+14*city*conf*(1-smooth(signal,.012,.06))
 raw,y=render(exposure)
 scale=np.maximum(yn,y)/np.maximum(y,1e-5)
 raw=np.clip(raw*scale[:,:,None],0,1)
 y=raw@WT
 saturation=.85-.4*(1-smooth(detail,.00017,.001))
 return np.uint16(np.round(np.clip(y[:,:,None]+(raw-y[:,:,None])*saturation[:,:,None],0,1)*65535))
def develop(mode):
 src=tifffile.imread(LINEAR);dest=np.memmap(B/'tone.mmap',mode='w+',dtype=np.uint16,shape=src.shape)
 for y0 in range(0,H,260):dest[y0:min(H,y0+260)]=tone(src[y0:min(H,y0+260)],mode,y0)
 jpeg(B/('DSC09818_TECH_NEUTRAL.jpg' if mode=='neutral' else 'DSC09818_TECH_HDR_TEST.jpg'),dest)
 if mode=='fusion':write(TEMP,dest,'Single RAW virtual exposure fusion; NOT real multishot HDR')
 del dest,src;gc.collect();os.remove(B/'tone.mmap')
def clean():
 src=tifffile.imread(TEMP);prev=cv2.resize(src,(2000,round(H*2000/W)),interpolation=cv2.INTER_AREA)
 p8=np.uint8(np.round(prev.astype(np.float32)/257));den=cv2.fastNlMeansDenoisingColored(np.ascontiguousarray(p8[:,:,::-1]),None,13,25,7,21)[:,:,::-1].copy();diff=den.astype(np.float32)-p8
 output=np.memmap(B/'clean.mmap',mode='w+',dtype=np.uint16,shape=src.shape);lowH,lowW=diff.shape[:2]
 for y0 in range(0,H,300):
  y1=min(H,y0+300);r0=max(0,y0-14);r1=min(H,y1+14);orig=src[r0:r1]
  u8=np.uint8(np.round(orig.astype(np.float32)/257));lab=cv2.cvtColor(u8,cv2.COLOR_RGB2LAB);l,a,b=cv2.split(lab)
  sm=cv2.cvtColor(cv2.merge([cv2.bilateralFilter(l,7,19,2.5),cv2.GaussianBlur(a,(7,7),1.6),cv2.GaussianBlur(b,(7,7),1.6)]),cv2.COLOR_LAB2RGB)
  mx=np.broadcast_to(np.linspace(0,lowW-1,W,dtype=np.float32),(r1-r0,W)).copy()
  my=np.broadcast_to(np.linspace(r0*(lowH-1)/(H-1),(r1-1)*(lowH-1)/(H-1),r1-r0,dtype=np.float32)[:,None],(r1-r0,W)).copy()
  low=cv2.remap(diff,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
  lum=l.astype(np.float32)/255;gx=cv2.Sobel(l,cv2.CV_32F,1,0,ksize=3);gy=cv2.Sobel(l,cv2.CV_32F,0,1,ksize=3);edge=np.clip((np.hypot(gx,gy)-38)/110,0,1)
  alpha=(.77-.4*np.clip((lum-.20)/.55,0,1))*(1-.42*edge);beta=(.5-.3*np.clip((lum-.12)/.65,0,1))*(1-.68*edge)
  delta=(sm.astype(np.float32)-u8.astype(np.float32))*alpha[:,:,None]+low*beta[:,:,None]
  output[y0:y1]=np.uint16(np.clip(orig.astype(np.float32)+257*delta,0,65535).round())[y0-r0:y1-r0]
 write(CLEAN,output,'DSC09818 technical CLEAN V9 16bit. Real RAW signal-recovery tone mapping + calibrated lens/PerspectiveLock + non-generative two-scale noise reduction')
 jpeg(B/'DSC09818_TECH_CLEAN.jpg',output)
 small=cv2.resize(output,(2560,round(H*2560/W)),interpolation=cv2.INTER_AREA)
 cv2.imwrite(str(B/'DSC09818_EDITORIAL_INPUT.jpg'),np.uint8(np.round(small.astype(np.float32)/257))[:,:,::-1],[cv2.IMWRITE_JPEG_QUALITY,94])
 del small
 del src,output;gc.collect();os.remove(B/'clean.mmap')
 return dict(clean16_sha256=sha(CLEAN),clean16_size=CLEAN.stat().st_size,technical_status='TECHNICAL_QA_PENDING',single_RAW_only=True,HDR_bracketed=False,no_generated_pixels=True,editorial_process_allowed=False,editorial_input_sha256=sha(B/'DSC09818_EDITORIAL_INPUT.jpg'))
if __name__=='__main__':
 meta=raw();record=geometry();develop('neutral');develop('fusion');record.update(clean())
 record.update(exif=meta,source_name='DSC09818.ARW',hdr_technology='single RAW virtual exposures, NOT true multi-exposure HDR',acknowledged_limits=['ISO3200 shadow noise','no exposure bracket','lens residual straightness not independently confirmed','tangential/chromatic aberration uncalibrated'],linear16_size=LINEAR.stat().st_size)
 # Independent same-coordinate pixel comparison ensures HDR cannot silently lose the city skyline signal.
 def roi_med(name):
  p=cv2.imread(str(B/name),cv2.IMREAD_COLOR)
  if p is None:raise RuntimeError("QA_missing_preview:"+name)
  h,w=p.shape[:2];roi=p[round(h*.37):round(h*.69),round(w*.61):round(w*.96)]
  lum=roi[:,:,2]*.2126+roi[:,:,1]*.7152+roi[:,:,0]*.0722
  return float(np.median(lum))/255
 neutral=roi_med('DSC09818_TECH_NEUTRAL.jpg')
 hdr=roi_med('DSC09818_TECH_HDR_TEST.jpg')
 clean=roi_med('DSC09818_TECH_CLEAN.jpg')
 if hdr < neutral*.97 or clean < neutral*.97:raise RuntimeError("HDR_CITY_SIGNAL_REGRESSION:"+str((neutral,hdr,clean)))
 record.update(roi_city_median_pct={k:round(v*100,4) for k,v in [('neutral',neutral),('hdr',hdr),('clean',clean)]},
  city_signal_qa="PASS",technical_status="TECHNICAL_QA_PASS",hdr_definition="single RAW tone extraction; not true multi-bracket HDR",
  editorial_process_allowed=False,release_scope="explicit-owner-direction-required-with-SHA-bound-release")
 J.write_text(json.dumps(record,indent=2,ensure_ascii=False));print('TECH_MASTER_DONE',json.dumps({k:v for k,v in record.items() if k in ('source_sha256','clean16_sha256','clean16_size','valid_fraction','technical_status')}))
 TEMP.unlink(missing_ok=True)
