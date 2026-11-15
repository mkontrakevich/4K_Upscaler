/* Non-destructive, lower-resolution local preview of the offline Perspective Lock.
   Matches the 4-corner server geometry algorithm. Not a final TIFF or QA pass. */
(function(root){
"use strict";
function hold(reason){const err=new Error(reason);err.code="GEOMETRY_HOLD";throw err}
function finite(x){return typeof x==="number"&&Number.isFinite(x)}
function validateQuad(q){
 if(!Array.isArray(q)||q.length!==4||q.some(p=>!Array.isArray(p)||p.length!==2||
   p.some(v=>!finite(v)||v<.015||v>.985)))hold("Отметьте четыре угла внутри фотографии");
 const [tl,tr,br,bl]=q;
 if(!(tl[0]<tr[0]&&bl[0]<br[0]&&tl[1]<bl[1]&&tr[1]<br[1]))
   hold("Проверьте порядок точек: верхний левый → верхний правый → нижний правый → нижний левый");
 const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);
 if(q.some((p,i)=>cross(p,q[(i+1)%4],q[(i+2)%4])<=.0025))
   hold("Выбранная плоскость вырождена или пересекается сама с собой");
 const area=Math.abs(q.reduce((v,p,i)=>v+p[0]*q[(i+1)%4][1]-p[1]*q[(i+1)%4][0],0)/2);
 if(area<.035||area>.78)hold("Отмеченная плоскость слишком мала или занимает почти весь кадр");
 const lx=(tl[0]+bl[0])/2,rx=(tr[0]+br[0])/2,ty=(tl[1]+tr[1])/2,by=(bl[1]+br[1])/2;
 if(rx-lx<.22||by-ty<.18)hold("Недостаточная ширина или высота фасада");
 if(Math.max(Math.abs(tl[0]-bl[0]),Math.abs(tr[0]-br[0]),
             Math.abs(tl[1]-tr[1]),Math.abs(bl[1]-br[1]))>.17)
   hold("Слишком сильная перспектива: нужна дополнительная экспертная разметка");
 return {src:q,target:[[lx,ty],[rx,ty],[rx,by],[lx,by]],area};
}
function solve(A,b){
 const x=A.map((row,i)=>[...row,b[i]]),n=b.length;
 for(let j=0;j<n;j++){
  let best=j;
  for(let i=j+1;i<n;i++)if(Math.abs(x[i][j])>Math.abs(x[best][j]))best=i;
  if(Math.abs(x[best][j])<1e-12)hold("Невозможно определить геометрическое преобразование");
  [x[best],x[j]]=[x[j],x[best]];
  const p=x[j][j];for(let k=j;k<=n;k++)x[j][k]/=p;
  for(let i=0;i<n;i++)if(i!==j){
   const f=x[i][j];for(let k=j;k<=n;k++)x[i][k]-=f*x[j][k];
  }
 }
 return x.map(row=>row[n]);
}
function homography(src,dst){
 const a=[],b=[];
 for(let i=0;i<4;i++){
  const [x,y]=src[i],[X,Y]=dst[i];
  a.push([x,y,1,0,0,0,-X*x,-X*y]);b.push(X);
  a.push([0,0,0,x,y,1,-Y*x,-Y*y]);b.push(Y);
 }
 const p=solve(a,b);return [p[0],p[1],p[2],p[3],p[4],p[5],p[6],p[7],1];
}
function invert(m){
 const [a,b,c,d,e,f,g,h,i]=m;
 const A=e*i-f*h,B=c*h-b*i,C=b*f-c*e,D=f*g-d*i,E=a*i-c*g,F=c*d-a*f,
       G=d*h-e*g,H=b*g-a*h,I=a*e-b*d;
 const det=a*A+b*D+c*G;
 if(!finite(det)||Math.abs(det)<1e-12)hold("Невозможно инвертировать геометрическую матрицу");
 return [A,B,C,D,E,F,G,H,I].map(v=>v/det);
}
function apply(m,x,y){
 const d=m[6]*x+m[7]*y+m[8];
 if(Math.abs(d)<1e-12)return [NaN,NaN];
 return [(m[0]*x+m[1]*y+m[2])/d,(m[3]*x+m[4]*y+m[5])/d];
}
function zoomed(m,z){return [z*m[0]+(.5*(1-z))*m[6],
 z*m[1]+(.5*(1-z))*m[7],z*m[2]+(.5*(1-z))*m[8],
 z*m[3]+(.5*(1-z))*m[6],z*m[4]+(.5*(1-z))*m[7],
 z*m[5]+(.5*(1-z))*m[8],m[6],m[7],m[8]]}
function plan(quad,width,height){
 if(!finite(width)||!finite(height)||width<320||height<320)hold("Недостаточное разрешение исходника");
 const v=validateQuad(quad),m=homography(v.src,v.target);
 let z=1,final=null,inv=null;
 for(let k=0;k<=48;k++){
  z=1+k*.0025;final=zoomed(m,z);inv=invert(final);
  const eps=Math.max(1/width,1/height);
  const points=[[eps,eps],[1-eps,eps],[1-eps,1-eps],[eps,1-eps]].map(p=>apply(inv,...p));
  if(points.every(([x,y])=>finite(x)&&finite(y)&&x>=eps&&x<=1-eps&&y>=eps&&y<=1-eps))break;
  inv=null;
 }
 if(!inv)hold("Исправление потребовало бы чрезмерной обрезки: сохранён исходник");
 let maxShift=0;const diag=Math.hypot(width,height);
 for(let j=0;j<=8;j++)for(let i=0;i<=8;i++){
  const x=i/8,y=j/8,[X,Y]=apply(final,x,y);
  maxShift=Math.max(maxShift,Math.hypot((X-x)*width,(Y-y)*height)/diag);
 }
 if(maxShift>.18)hold("Слишком сильная деформация кадра: нужна ручная проверка");
 if(maxShift<.0025)hold("Геометрия уже близка к прямоугольной: значимой коррекции нет");
 return {matrix:final,inverse:inv,cropZoom:z,cropFraction:1-1/z**2,
   maxNormalizedDisplacement:maxShift,quad,target:v.target};
}
function render(image,originalCanvas,correctedCanvas,quad){
 if(!image?.naturalWidth||!image?.naturalHeight)hold("Исходная фотография не загружена");
 const p=plan(quad,image.naturalWidth,image.naturalHeight);
 const factor=Math.min(1,920/Math.max(image.naturalWidth,image.naturalHeight));
 const w=Math.max(1,Math.round(image.naturalWidth*factor)),
       h=Math.max(1,Math.round(image.naturalHeight*factor));
 originalCanvas.width=correctedCanvas.width=w;
 originalCanvas.height=correctedCanvas.height=h;
 const oc=originalCanvas.getContext("2d",{willReadFrequently:true});
 const cc=correctedCanvas.getContext("2d");
 if(!oc||!cc)hold("Браузер не поддерживает проверку геометрии");
 oc.clearRect(0,0,w,h);oc.drawImage(image,0,0,w,h);
 const src=oc.getImageData(0,0,w,h);
 const out=cc.createImageData(w,h);
 const si=src.data,di=out.data,inv=p.inverse;
 for(let y=0;y<h;y++){
  const ny=(y+.5)/h;
  for(let x=0;x<w;x++){
   const nx=(x+.5)/w,den=inv[6]*nx+inv[7]*ny+inv[8];
   if(Math.abs(den)<1e-10)hold("Неустойчивое геометрическое преобразование");
   const ax=(inv[0]*nx+inv[1]*ny+inv[2])/den*w-.5;
   const ay=(inv[3]*nx+inv[4]*ny+inv[5])/den*h-.5;
   if(ax<-.5||ay<-.5||ax>w-.5||ay>h-.5)
     hold("Изображение имеет незаполненные края: требуется дополнительная проверка");
   const u=Math.max(0,Math.min(w-1,ax)),v=Math.max(0,Math.min(h-1,ay));
   const x0=Math.floor(u),y0=Math.floor(v),x1=Math.min(w-1,x0+1),y1=Math.min(h-1,y0+1);
   const fx=u-x0,fy=v-y0,d=(y*w+x)*4;
   const p00=(y0*w+x0)*4,p10=(y0*w+x1)*4,p01=(y1*w+x0)*4,p11=(y1*w+x1)*4;
   for(let c=0;c<4;c++){
    di[d+c]=Math.round(
     si[p00+c]*(1-fx)*(1-fy)+si[p10+c]*fx*(1-fy)+
     si[p01+c]*(1-fx)*fy+si[p11+c]*fx*fy);
   }
  }
 }
 cc.putImageData(out,0,0);
 return p;
}
root.MG4KGeometryPreview={validateQuad,plan,render};
})(typeof window!=="undefined"?window:globalThis);
