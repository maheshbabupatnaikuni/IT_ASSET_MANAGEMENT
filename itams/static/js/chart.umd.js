/* Offline Chart.js-compatible renderer used by the ITAMS dashboard. */
(function(global){
  'use strict';
  class Chart {
    constructor(canvas,config){
      if(!canvas||!canvas.getContext) throw new Error('Chart canvas is unavailable');
      this.canvas=canvas;this.config=config||{};this.render();
    }
    render(){
      const canvas=this.canvas,ctx=canvas.getContext('2d'),data=this.config.data||{},dataset=(data.datasets||[])[0]||{},labels=data.labels||[],values=(dataset.data||[]).map(value=>Number(value)||0),colors=dataset.backgroundColor||['#4f46e5'];
      const width=Math.max(canvas.clientWidth||500,300),height=250,ratio=global.devicePixelRatio||1;canvas.width=width*ratio;canvas.height=height*ratio;canvas.style.height=height+'px';ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
      if(this.config.type==='doughnut') this.drawDoughnut(ctx,width,height,labels,values,colors); else if(this.config.options&&this.config.options.indexAxis==='y') this.drawHorizontal(ctx,width,height,labels,values,colors); else this.drawBars(ctx,width,height,labels,values,colors);
    }
    drawBars(ctx,width,height,labels,values,colors){
      const max=Math.max(...values,1),left=36,bottom=42,plotWidth=width-left-12,plotHeight=height-bottom-12,count=Math.max(values.length,1),gap=8,barWidth=Math.max((plotWidth/count)-gap,4);ctx.font='11px Arial';ctx.textAlign='center';
      values.forEach((value,index)=>{const x=left+index*(plotWidth/count)+gap/2,barHeight=(plotHeight*value)/max;ctx.fillStyle=colors[index%colors.length];ctx.fillRect(x,height-bottom-barHeight,barWidth,barHeight);ctx.fillStyle='#475569';ctx.fillText(String(value),x+barWidth/2,height-bottom-barHeight-5);ctx.fillText(String(labels[index]||'').slice(0,14),x+barWidth/2,height-18)});
    }
    drawHorizontal(ctx,width,height,labels,values,colors){
      const max=Math.max(...values,1),left=Math.min(Math.max(...labels.map(label=>String(label).length),8)*7+14,150),right=34,top=10,plotWidth=width-left-right,count=Math.max(values.length,1),rowHeight=Math.max((height-top-10)/count,16),barHeight=Math.max(rowHeight-7,8);ctx.font='11px Arial';ctx.textAlign='right';
      values.forEach((value,index)=>{const y=top+index*rowHeight,barWidth=plotWidth*value/max;ctx.fillStyle='#475569';ctx.fillText(String(labels[index]||''),left-7,y+barHeight*.72);ctx.fillStyle=colors[index%colors.length];ctx.fillRect(left,y,barWidth,barHeight);ctx.fillStyle='#172033';ctx.textAlign='left';ctx.fillText(String(value),left+barWidth+5,y+barHeight*.72);ctx.textAlign='right'});
    }
    drawDoughnut(ctx,width,height,labels,values,colors){
      const total=values.reduce((sum,value)=>sum+value,0),cx=Math.min(width*.32,150),cy=height/2,radius=82,inner=46;let angle=-Math.PI/2;ctx.font='12px Arial';
      values.forEach((value,index)=>{const slice=total?value/total*Math.PI*2:0;ctx.beginPath();ctx.arc(cx,cy,radius,angle,angle+slice);ctx.arc(cx,cy,inner,angle+slice,angle,true);ctx.closePath();ctx.fillStyle=colors[index%colors.length];ctx.fill();angle+=slice;const y=28+index*28;ctx.fillRect(Math.min(width*.58,270),y-10,12,12);ctx.fillStyle='#475569';ctx.textAlign='left';ctx.fillText(`${labels[index]}: ${value}`,Math.min(width*.58,270)+18,y)});
      ctx.fillStyle='#172033';ctx.textAlign='center';ctx.font='bold 18px Arial';ctx.fillText(String(total),cx,cy+6);
    }
  }
  global.Chart=Chart;
})(window);
