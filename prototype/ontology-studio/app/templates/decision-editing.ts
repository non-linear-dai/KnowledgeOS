import type {DecisionCell,DecisionColumn,DecisionTable} from './template-api';

export function decisionValue(text:string,type:DecisionColumn['type']):string|boolean {
  const value=text.trim();
  if(type==='boolean'){if(value==='true')return true;if(value==='false')return false;throw new Error('布尔值请输入 true 或 false');}
  if(type==='number'&&!/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value))throw new Error('请输入有限的十进制数');
  return type==='string'?text:value;
}
export function parseDecisionCell(text:string,type:DecisionColumn['type']):DecisionCell {
  const value=text.trim(); if(!value||value==='*')return null;
  const range=value.match(/^\[(.+)\.\.(.+)\]$/);
  if(range){if(type!=='number')throw new Error('范围只适用于数值列');return{op:'between',value:[decisionValue(range[1],type),decisionValue(range[2],type)]};}
  if(value.includes('|'))return{op:'in',value:value.split('|').map(v=>decisionValue(v,type))};
  const comparison=value.match(/^(>=|<=|!=|>|<|=)\s*(.*)$/);
  const ops={'>=':'gte','<=':'lte','!=':'ne','>':'gt','<':'lt','=':'eq'} as const;
  if(comparison){const op=ops[comparison[1] as keyof typeof ops];if(!['eq','ne'].includes(op)&&type!=='number')throw new Error('大小比较只适用于数值列');return{op,value:decisionValue(comparison[2],type)};}
  return {op:'eq',value:decisionValue(value,type)};
}
export function renderDecisionCell(cell:DecisionCell):string {
  if(!cell)return '*';if(cell.op==='between')return `[${(cell.value as unknown[]).join('..')}]`;if(cell.op==='in')return (cell.value as unknown[]).join('|');
  return `${{eq:'',ne:'!=',gt:'>',gte:'>=',lt:'<',lte:'<='}[cell.op]}${cell.value}`;
}
export function emptyDecision(id:string):DecisionTable {
  return{id,version:'1.0.0',label:'新决策表',hit_policy:'UNIQUE',inputs:[{id:'input_1',field:'feature',source:'facts',type:'number'}],outputs:[{id:'enabled',type:'boolean'}],rules:[{id:'rule_1',when:{input_1:{op:'gte',value:'1'}},then:{enabled:true}}],default_output:{enabled:false}};
}
