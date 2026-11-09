#!/usr/bin/env python3
"""8-bit, full-pixel-resolution browser proxy derived from 16-bit Technical V10 TIFF.
Never upsample: spatial pixels remain 6536x4360. The TIFF in R2 is immutable.
"""
import hashlib,json,sys
from pathlib import Path
import numpy as np,tifffile
from PIL import Image
def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as file:
  for block in iter(lambda:file.read(8*1024*1024),b""):h.update(block)
 return h.hexdigest()
def main(src,qa_path,out,manifest):
 qa=json.loads(Path(qa_path).read_text())
 if qa.get("technical_status")!="TECHNICAL_QA_PASS" or qa.get("technical_version")!="V10_NEUTRAL_NO_HDR" or qa.get("HDR_applied") is not False or qa.get("virtual_exposures") is not False or qa.get("owner_QA_approved") is not False or qa.get("source_sha256")!="1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5":raise RuntimeError("INVALID_TECHNICAL_QA")
 if sha(src)!=qa["clean16_sha256"]:raise RuntimeError("TIFF_SOURCE_SHA_MISMATCH")
 with tifffile.TiffFile(src) as f:
  p=f.pages[0]
  if p.shape!=(4360,6536,3) or p.dtype.name!="uint16":raise RuntimeError("INVALID_PIXEL_SHAPE_OR_BIT_DEPTH")
 img=tifffile.imread(src)
 # Preserve exact spatial resolution; browser sees an 8-bit visualization of the 16-bit technical master.
 browser=((img.astype(np.uint32)+128)//257).astype(np.uint8)
 picture=Image.fromarray(browser,mode="RGB")
 picture.save(out,format="JPEG",quality=96,subsampling=0,optimize=True,progressive=True)
 proof=dict(project="tropicana-park",frame="DSC09818",stage="TECHNICAL_MASTER_V10",original_arw_sha256=qa["source_sha256"],
            master16_sha256=qa["clean16_sha256"],preview_sha256=sha(out),
            preview_key="tropicana-park/DSC09818/technical-v10/fullres-review-"+qa["clean16_sha256"][:16]+".jpg",
            native_width=6536,native_height=4360,master_bit_depth=16,display_bit_depth=8,
            preview_bytes=Path(out).stat().st_size,source_unchanged=True,no_upscale=True,
            truthful_label="Full spatial resolution, 8-bit screen proxy of 16-bit RAW technical V10 TIFF")
 Path(manifest).write_text(json.dumps(proof,indent=2,ensure_ascii=False),encoding="utf-8")
 print(json.dumps(proof,ensure_ascii=False))
if __name__=="__main__":
 if len(sys.argv)!=5:raise SystemExit("usage: python editorial_fullres_review_v10.py <TIFF> <QA.json> <output.jpg> <manifest.json>")
 main(*sys.argv[1:])
