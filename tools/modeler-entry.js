import BpmnModeler from 'bpmn-js/lib/Modeler';
import TokenSimulationModule from 'bpmn-js-token-simulation';
import 'bpmn-js-token-simulation/assets/css/bpmn-js-token-simulation.css';

window.BpmnJS = class extends BpmnModeler {
  constructor(options) {
    super({ ...options, additionalModules: [TokenSimulationModule, ...(options.additionalModules || [])] });
  }
};
