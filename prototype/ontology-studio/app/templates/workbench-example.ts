import type {ExpertTemplate,CostScenario,OperationTemplate} from './template-api';
import {nextRouteKey} from './route-editing';

/** Fictional regression example; rates live only in the trial scenario. */
export function workbenchExample(routeIds:string[],operationIds:string[],decisionIds:string[]):{template:ExpertTemplate;scenario:CostScenario}{
  const used=[...operationIds];const key=(prefix:string)=>{const id=nextRouteKey(prefix,used);used.push(id);return id;};
  const prepare=key('demo_prepare'),rough=key('demo_rough'),fine=key('demo_fine'),polish=key('demo_polish'),inspect=key('demo_inspect');
  const decision=nextRouteKey('demo_method',decisionIds),ref=`decision:${decision}:1.0.0`;
  const timed=(id:string,label:string,minutes:string):OperationTemplate=>({id,version:'1.0.0',label,cost_basis:'piece',processing:{batch_size:'10',duration:{value:minutes,unit:'minute'},setup:{value:'15',unit:'minute'}},resources:[
    {kind:'equipment',ref:'machine',amount:'1',unit:'hour',basis:'time'},{kind:'labor',ref:'operator',amount:'1',unit:'hour',basis:'time'},{kind:'facility',ref:'workshop',amount:'1',unit:'hour',basis:'time'},
    {kind:'energy',ref:'electricity',amount:'1',unit:'kWh',basis:'batch',quantity_model_ref:'operation_energy@1.0.0',quantity_inputs:{effective_power:{source:'facts',field:'machine_power'},elapsed_time:{source:'process',field:'duration_seconds'}}}]});
  const fineOperation=timed(fine,'精铣','30');fineOperation.time_model_ref='operation_cycle_time@1.0.0';fineOperation.processing={batch_size:'10',setup:{value:'30',unit:'minute'},time_inputs:{baseline_time:{source:'facts',field:'baseline_time'},workload_factor:{source:'facts',field:'workload_factor'}}};
  return{template:{id:nextRouteKey('route',routeIds),version:'1.0.0',label:'决策与成本联动示例',family:'machining',output_basis:'piece',summary:'虚构的专家回归案例；价格和产量仅用于本次试算。',
    groups:[{id:'faces',label:'逐面加工',iteration_set:'surfaces',join_policy:'all',group_mode:'repeat',execution_mode:'parallel'}],
    steps:[{id:'prepare',label:'备料',parent:null,operation_ref:`operation:${prepare}:1.0.0`,cost_basis:'piece'},
      {id:'mill',label:'铣削（决策选择）',parent:'faces',operation_ref:`operation:${rough}:1.0.0`,cost_basis:'piece',decision_binding:{ref,output:'method',purpose:'operation'}},
      {id:'polish',label:'表面精整（按需）',parent:'faces',operation_ref:`operation:${polish}:1.0.0`,cost_basis:'piece',decision_binding:{ref,output:'finish',purpose:'enabled',equals:true}},
      {id:'inspect',label:'整件检验',parent:null,operation_ref:`operation:${inspect}:1.0.0`,cost_basis:'piece'}],
    edges:[{from:'prepare',to:'faces'},{from:'mill',to:'polish'},{from:'faces',to:'inspect'}],
    decisions:[{id:decision,version:'1.0.0',label:'精度与精整需求',hit_policy:'UNIQUE',inputs:[{id:'precision',source:'facts',field:'precision_level',type:'number'},{id:'finishing',source:'facts',field:'requires_finish',type:'boolean'}],outputs:[{id:'method',type:'string'},{id:'finish',type:'boolean'}],rules:[
      {id:'standard',when:{precision:{op:'lt',value:'2'},finishing:null},then:{method:`operation:${rough}:1.0.0`,finish:false}},
      {id:'precision_finish',when:{precision:{op:'gte',value:'2'},finishing:{op:'eq',value:true}},then:{method:`operation:${fine}:1.0.0`,finish:true}},
      {id:'precision_plain',when:{precision:{op:'gte',value:'2'},finishing:{op:'eq',value:false}},then:{method:`operation:${fine}:1.0.0`,finish:false}}]}],
    operations:[{id:prepare,version:'1.0.0',label:'备料',cost_basis:'piece',resources:[{kind:'material',ref:'blank',amount:'2',unit:'one',basis:'piece'}]},timed(rough,'粗铣','15'),fineOperation,
      {id:polish,version:'1.0.0',label:'表面精整',cost_basis:'piece',processing:{batch_size:'10',duration:{value:'5',unit:'minute'}},resources:[{kind:'labor',ref:'operator',amount:'1',unit:'hour',basis:'time'}]},
      {id:inspect,version:'1.0.0',label:'整件检验',cost_basis:'piece',processing:{batch_size:'1',duration:{value:'1',unit:'minute'}},resources:[{kind:'labor',ref:'operator',amount:'1',unit:'hour',basis:'time'}]}],
    cases:[{id:'standard',facts:{precision_level:1,requires_finish:true,baseline_time:{literal:'15',unit:'minute'},workload_factor:'2',machine_power:{literal:'2',unit:'kW'}},sets:{surfaces:[{id:'face_a'},{id:'face_b'}]},expected_operations:4},
      {id:'precision',facts:{precision_level:2,requires_finish:true,baseline_time:{literal:'15',unit:'minute'},workload_factor:'2',machine_power:{literal:'2',unit:'kW'}},sets:{surfaces:[{id:'face_a'},{id:'face_b'}]},expected_operations:6}]},
    scenario:{quantity:'20',currency:'CNY',margin:'0.1',operating_ratio:'0.1',rates:{blank:{value:'3',unit:'one',currency:'CNY'},machine:{value:'120',unit:'hour',currency:'CNY'},operator:{value:'60',unit:'hour',currency:'CNY'},workshop:{value:'10',unit:'hour',currency:'CNY'},electricity:{value:'0.8',unit:'kWh',currency:'CNY'}}}};
}
