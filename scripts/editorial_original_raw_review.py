#!/usr/bin/env python3
"""Create a full-pixel-resolution, neutral viewer image of the original DSC09818.ARW.
No lens distortion, no perspective, no HDR, no denoising, no rescaling or generated pixels.
Technically correct WB/demosaic and common neutral sRGB tone for a usable monitor preview.
The actual original remains the immutable 35MB Sony ARW; output is NOT a RAW file.
"""
import hashlib,json,sys
from pathlib import Path
import numpy as np,rawpy,tifffile
from PIL import Image
SOURCE_SHA="1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5"
W,H=7028,4688
def digest(path):
 sha=hashlib.sha256()
 with open(path,"rb") as f:
  for block in iter(lambda:f.read(8*1024*1024),b""):sha.update(block)
 return sha.hexdigest()
def main(raw_path,out,manifest):
 raw_path,out,manifest=map(Path,(raw_path,out,manifest))
 if digest(raw_path)!=SOURCE_SHA:raise RuntimeError("original_RAW_SHA_mismatch")
 with tifffile.TiffFile(raw_path) as file:
  tags=file.pages[0].tags
  ex=tags["ExifTag"].value
  focal=ex["FocalLength"];focal=focal[0]/focal[1]
  camera=tags["Model"].value; lens=ex["LensModel"]
  if camera!="ILCE-7M4" or lens!="FE 16-35mm F2.8 GM" or focal!=16:raise RuntimeError("RAW_EXIF_unexpected")
 with rawpy.imread(str(raw_path)) as r:
  rgb=r.postprocess(use_camera_wb=True,no_auto_bright=True,output_bps=16,gamma=(1,1),
    highlight_mode=rawpy.HighlightMode.Blend,output_color=rawpy.ColorSpace.sRGB)
 if rgb.shape!=(H,W,3) or rgb.dtype!=np.uint16:raise RuntimeError("Unexpected_RAW_dimensions_or_depth")
 neutral=np.empty((H,W,3),dtype=np.uint8)
 # Exactly the neutral tone used in V9: avoids confusing tone differences with geometry corrections.
 for y0 in range(0,H,192):
  y1=min(H,y0+192)
  v=rgb[y0:y1].astype(np.float32)/65535
  lum=(v[:,:,0]*.2126+v[:,:,1]*.7152+v[:,:,2]*.0722)
  signal=np.maximum(lum-.000105,0)
  t=np.power(signal*32/(signal*32+.195),.86)
  m=np.clip(v*(t/np.maximum(lum,.0001))[:,:,None],0,1)
  neutral[y0:y1]=np.uint8(np.clip(np.rint(m*255),0,255))
 Image.fromarray(neutral,"RGB").save(out,format="JPEG",quality=96,subsampling=0,progressive=True,optimize=True)
 output=dict(project="tropicana-park",frame="DSC09818",stage="ORIGINAL_RAW_NEUTRAL_UNRECTIFIED",
  original_filename="DSC09818.ARW",original_raw_sha256=SOURCE_SHA,
  original_camera=camera,original_lens=lens,original_focal_mm=focal,
  original_sensor_width=W,original_sensor_height=H,display_width=W,display_height=H,
  display_bit_depth=8,source_format="SONY_ARW",source_demosaic_precision=16,
  display_sha256=digest(out),display_bytes=out.stat().st_size,
  display_key="tropicana-park/DSC09818/original-raw/fullres-neutral-"+SOURCE_SHA[:16]+"-"+digest(out)[:16]+".jpg",
  technical_reference_size=[6536,4360],reference_center_crop_xywh=[246,164,6536,4360],
  optical_distortion_corrected=False,perspective_corrected=False,HDR_applied=False,denoise_applied=False,
  neutral_tone="same neutral scene-linear exposure curve as TECH V9 (E=32,gamma=.86)",
  no_spatial_resize=True,no_creative_processing=True,
  disclaimer="8-bit full-resolution neutral JPEG display proxy developed from actual Sony ARW. RAW remains immutable in private R2.")
 manifest.write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding="utf8")
 print(json.dumps(output,ensure_ascii=False))
if __name__=="__main__":
 if len(sys.argv)!=4:raise SystemExit("usage: script.py ORIGINAL.ARW out.jpg manifest.json")
 main(*sys.argv[1:])
