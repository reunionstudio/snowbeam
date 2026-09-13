/* Run with Node + sharp. --modules can point to an existing node_modules directory. */
const fs = require('node:fs');
const path = require('node:path');
const design = require('./design.cjs');
const root = path.resolve(__dirname, '../..');
const moduleIndex = process.argv.indexOf('--modules');
const sharp = require(require.resolve('sharp', moduleIndex >= 0 ? {paths:[process.argv[moduleIndex+1]]} : undefined));
const asset = path.join(root,'docs/brand/vector');
const docs = path.join(root,'docs/brand');
const framesIndex = process.argv.indexOf('--frames');
async function main(){
 fs.mkdirSync(asset,{recursive:true}); fs.mkdirSync(docs,{recursive:true});
 const svg=design.render(1.85,{poster:true});
 fs.writeFileSync(path.join(asset,'snowbeam.svg'),svg+'\n');
 fs.writeFileSync(path.join(docs,'snowbeam-mark.svg'),design.render(1.85,{badge:false,poster:true})+'\n');
 for(const size of [16,24,32,48,64,128,256,512]){
  await sharp(Buffer.from(svg)).resize(size,size).png().toFile(path.join(asset,`snowbeam-${size}.png`));
 }
 const sizes=[16,32,48,64].map(size=>`<span>${design.render(1.85,{poster:true,id:'size'+size}).replace('width="512" height="512"',`width="${size}" height="${size}"`)}${size}px</span>`).join('');
 const preview=fs.readFileSync(path.join(__dirname,'preview.html'),'utf8')
  .replace('__POSTER__',svg).replace('__SIZES__',sizes)
  .replace('__DESIGN__',fs.readFileSync(path.join(__dirname,'design.cjs'),'utf8'));
 fs.writeFileSync(path.join(docs,'preview.html'),preview);
 for(const [label,time] of [['standing',.6],['beam',1.85],['vanishing',2.5],['gone',3.4],['snow',4.8]]){
  await sharp(Buffer.from(design.render(time))).resize(320,320).png().toFile(path.join(docs,label+'.png'));
 }
 if(framesIndex>=0){
  const directory=path.resolve(process.argv[framesIndex+1]);fs.mkdirSync(directory,{recursive:true});
  for(let frame=0;frame<150;frame++){
   await sharp(Buffer.from(design.render(frame/15))).resize(360,360).flatten({background:'#08131b'}).png().toFile(path.join(directory,String(frame).padStart(4,'0')+'.png'));
  }
 }
 console.log('Built archived vector SVG, PNGs, motion preview, and storyboard frames.');
}
main().catch(error=>{console.error(error.message);process.exitCode=1});
