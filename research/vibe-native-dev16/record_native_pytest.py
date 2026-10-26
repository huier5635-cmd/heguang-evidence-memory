"""Python 3.7 compatible recorder for the fixed solver-visible native pytest argv."""
import argparse,json
from pathlib import Path
import pytest

class Recorder:
    def __init__(self,output,command):
        self.output=output;self.command=command;self.events=[];self.collection_errors=0;self.diagnostic_bytes=0;self.collection_diagnostics=[]
    def bounded_failure(self,report):
        detail=str(report.longrepr)
        limit=max(0,min(12000,262144-self.diagnostic_bytes))
        retained=detail.encode('utf-8')[:limit].decode('utf-8',errors='ignore')
        self.diagnostic_bytes+=len(retained.encode('utf-8'))
        return retained,len(retained.encode('utf-8')) < len(detail.encode('utf-8'))
    def pytest_runtest_logreport(self,report):
        event={'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,'wasxfail':hasattr(report,'wasxfail'),
               'report_type':type(report).__module__+'.'+type(report).__name__}
        if type(report).__name__ in {'SubtestReport','SubTestReport'} and hasattr(report,'context'):
            context=report.context
            event['is_subtest']=True
            event['subtest_context']=json.dumps({'msg':context.msg,'kwargs':context.kwargs},sort_keys=True,default=repr,ensure_ascii=True)
        if report.failed:
            retained,truncated=self.bounded_failure(report)
            event['failure_diagnostic']=retained
            event['failure_diagnostic_truncated']=truncated
        self.events.append(event)
    def pytest_collectreport(self,report):
        self.collection_errors+=int(report.failed)
        if report.failed:
            retained,truncated=self.bounded_failure(report)
            self.collection_diagnostics.append({'nodeid':report.nodeid,'diagnostic':retained,'truncated':truncated})
    def pytest_sessionfinish(self,session,exitstatus):
        record={'collection_diagnostics':self.collection_diagnostics,'diagnostic_scope':'bounded failure diagnostics frozen before oracle access; no test selection changes','collected':[item.nodeid for item in session.items],'events':self.events,'collection_errors':self.collection_errors,'exit_code':int(exitstatus),'native_command':self.command,'test_selection_changed':False,'reporting_only_argument_added':True,'official_score':False,'full_allowed':False}
        with (self.output/'native-events.json').open('x',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2);stream.write('\n')

def main():
    p=argparse.ArgumentParser();p.add_argument('--commands',type=Path,required=True);p.add_argument('--case',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    commands=json.loads(args.commands.read_text(encoding='utf-8'));assert commands['frozen_before_oracle_access']
    command=commands['commands'][args.case]['argv']
    if (not isinstance(command,list) or len(command)<2 or command[0]!='pytest'
            or any(not isinstance(v,str) or '\x00' in v for v in command)
            or any(v.startswith('--junitxml') or v.startswith('--junit-xml') for v in command)):
        raise ValueError('Unsupported native pytest command')
    args.output.mkdir(parents=True,exist_ok=False)
    return pytest.main(command[1:]+['--junitxml='+str((args.output/'native.xml').resolve())],plugins=[Recorder(args.output,command)])
if __name__=='__main__':raise SystemExit(main())
