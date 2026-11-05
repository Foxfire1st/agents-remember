"""D649 native window resolution, independent of the destination check."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError

from .adapters.execution import LaunchProfile
from .artifacts import write_object
from .hermes_profiles import hermes_route
from .hermes_runtime import HermesRuntimeBinding

_WINDOW_PROBE = r"""
import contextlib,hashlib,io,json,os,sys
from pathlib import Path
from urllib.parse import parse_qsl,urlsplit
source,home=map(Path,sys.argv[1:3]);home=home.resolve()
provider,model,expected=sys.argv[3:6]
private=[Path(os.environ[k]).resolve() for k in
 ('HOME','HERMES_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME','TMPDIR')
 if os.environ.get(k)]
blocked=[]
catalog_active=False
window_lookup=False
catalog_addresses=set()
catalog_requests=0
catalog_profile=(provider,model,expected)==('openai-codex','gpt-6.1-sol-900k','chatgpt.com')
admission={
    'name':'hermes-gpt-access-only-window-catalog',
    'authority':'MIK-L62 Manager 20261102T071120Z',
    'relatedExceptions':['F10 fixture skill policy','D647 auxiliary-task seed'],
    'profile':'hermes-gpt','host':'chatgpt.com','purpose':'window lookup',
    'credentialSource':'trial HERMES_HOME access-only manual pool',
    'method':'GET','path':'/backend-api/codex/models',
    'otherHostsForbidden':True,'redirects':'refused','proxyEnvironment':'not used'
} if catalog_profile else None

def refuse(event):
    blocked.append(event)
    raise PermissionError('window probe scope')

def guard(event,args):
    if (event=='subprocess.Popen' and args[1]==['git','rev-parse','HEAD']
            and args[2] is not None and Path(args[2]).resolve()==source.resolve()):
        return
    if event=='subprocess.Popen' or (
            event in ('socket.connect','socket.connect_ex','socket.getaddrinfo')):
        if catalog_active and event=='socket.getaddrinfo' and args[:2]==('chatgpt.com',443):
            return
        if (catalog_active and event in ('socket.connect','socket.connect_ex')
                and isinstance(args[1],tuple) and len(args[1])>=2
                and args[1][0] in catalog_addresses and args[1][1]==443):
            return
        refuse(event)
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=Path(os.fsdecode(args[0])).absolute()
        if path in [source.parent/n for n in ('config.yaml','.env','auth.json','auth.lock')]:
            blocked.append('real-home-read');raise PermissionError('private home only')
        mode,flags=args[1:3]
        writing=isinstance(mode,str) and any(c in mode for c in 'wax+')
        writing|=isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT))
        if writing and not any(path.resolve().is_relative_to(root) for root in private):
            blocked.append('outside-private-write');raise PermissionError('private writes only')
def access_only_token():
    # Read only this trial's already-installed pool; never discover another store.
    try:
        with (home/'auth.json').open('rb') as stream:
            raw=stream.read(131073)
        if len(raw)>131072:refuse('window-catalog-pool-size')
        document=json.loads(raw)
    except (OSError,ValueError):refuse('window-catalog-pool-unreadable')
    if not isinstance(document,dict):refuse('window-catalog-pool-identity')
    pending=[document];nodes=0
    while pending:
        item=pending.pop();nodes+=1
        if nodes>512:refuse('window-catalog-pool-size')
        if isinstance(item,dict):
            if 'refresh_token' in item:refuse('window-catalog-refresh-token')
            pending.extend(item.values())
        elif isinstance(item,list):pending.extend(item)
    pool=document.get('credential_pool',{})
    rows=pool.get('openai-codex') if isinstance(pool,dict) else None
    if (not isinstance(pool,dict) or set(pool)!={'openai-codex'}
            or document.get('providers') not in (None,{})
            or not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict)):
        refuse('window-catalog-pool-identity')
    row=rows[0]
    if (row.get('id'),row.get('source'),row.get('auth_type'))!=('trial-access','manual','oauth'):
        refuse('window-catalog-pool-identity')
    token=row.get('access_token')
    if not isinstance(token,str) or not token:refuse('window-catalog-pool-token')
    return token

@contextlib.contextmanager
def catalog_stream(url,*,headers=None,params=None,timeout=10.0,verify=None):
    global catalog_active,catalog_requests
    import httpx
    try:
        parsed=urlsplit(url);port=parsed.port
    except ValueError:refuse('window-catalog-request-outside-admission')
    query=parse_qsl(parsed.query,keep_blank_values=True)
    if (not catalog_profile or not window_lookup or parsed.scheme!='https'
            or parsed.hostname!='chatgpt.com' or port not in (None,443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or parsed.path!='/backend-api/codex/models'
            or any(key!='client_version' for key,value in query) or params):
        refuse('window-catalog-request-outside-admission')
    token=access_only_token()
    authorizations=[value for key,value in (headers or {}).items() if key.lower()=='authorization']
    if runtime.get('api_key')!=token or authorizations!=['Bearer '+token]:
        refuse('window-catalog-credential-not-trial-access')
    if isinstance(timeout,tuple):
        connect,read=timeout;timeout=httpx.Timeout(read,connect=connect)
    catalog_active=True
    try:
        # The native metadata helper follows redirects. This probe's admitted
        # transport never does, and ignores proxy routing to any other host.
        with httpx.Client(verify=verify,timeout=timeout,follow_redirects=False,trust_env=False) as client:
            catalog_requests+=1
            with client.stream('GET',url,headers=headers,params=params) as response:
                if 300<=response.status_code<400:refuse('window-catalog-redirect')
                yield response
    finally:
        catalog_active=False;catalog_addresses.clear()

def install_catalog_transport(metadata):
    import socket
    original=socket.getaddrinfo
    def lookup(host,port,*args,**kwargs):
        if catalog_active and (host,port)!=('chatgpt.com',443):
            refuse('window-catalog-dns-outside-admission')
        answers=original(host,port,*args,**kwargs)
        if catalog_active:
            catalog_addresses.update(answer[4][0] for answer in answers if answer[4][1]==443)
        return answers
    socket.getaddrinfo=lookup
    metadata.model_metadata_http.stream=catalog_stream

sys.addaudithook(guard);sys.path.insert(0,str(source))
result={'status':'native-window-failed'}
try:
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        from hermes_constants import get_hermes_home,get_default_hermes_root
        from hermes_cli.env_loader import load_hermes_dotenv
        if get_hermes_home().resolve()!=home or get_default_hermes_root().resolve()!=home:
            raise ValueError('private home binding differs')
        load_hermes_dotenv(hermes_home=get_hermes_home())
        from hermes_cli.runtime_provider import resolve_runtime_provider
        runtime=resolve_runtime_provider(requested=provider,target_model=model)
        if urlsplit(runtime['base_url']).hostname!=expected:
            raise ValueError('destination differs before window resolution')
        from agent import model_metadata as metadata
        from hermes_cli.config import load_config_readonly,get_compatible_custom_providers
        cfg=load_config_readonly()
        returns=[]
        def observe(frame,event,value):
            if (event=='return' and frame.f_code.co_name=='get_model_context_length'
                    and Path(frame.f_code.co_filename)==source/'agent/model_metadata.py'):
                returns.append({'path':'agent/model_metadata.py','line':frame.f_lineno})
        if catalog_profile:install_catalog_transport(metadata)
        sys.setprofile(observe)
        window_lookup=True
        try:
            window=metadata.get_model_context_length(
                model,base_url=runtime['base_url'],api_key=runtime.get('api_key') or '',
                provider=provider,custom_providers=get_compatible_custom_providers(cfg))
        finally:
            window_lookup=False;sys.setprofile(None)
        result={'status':'resolved','resolvedWindow':window,
                'windowSource':{'kind':'resolver',
                  'function':'agent.model_metadata.get_model_context_length',
                  'provider':provider,'model':model,'nativeReturn':returns,
                  'networkAllowed':catalog_profile,
                  'sourceSha256':hashlib.sha256((source/'agent/model_metadata.py').read_bytes()).hexdigest()}}
except Exception as error:
    result={'status':'native-window-failed','errorType':type(error).__name__}
if blocked:result={'status':'scope-guard-refused','blockedEvents':sorted(set(blocked))}
if admission is not None:result['scopeAdmission']={**admission,'requestsAdmitted':catalog_requests}
print(json.dumps(result))
"""


def resolve_hermes_window(
    runtime: HermesRuntimeBinding,
    environment: dict[str, str],
    profile: LaunchProfile,
    *,
    receipt_path: Path,
) -> dict[str, Any]:
    """Resolve through Hermes with the admitted GPT-only catalog boundary."""
    route = hermes_route(profile)
    started = time.monotonic()
    result: dict[str, Any] = {"status": "native-window-failed", "limitSeconds": 60}
    try:
        done = subprocess.run(
            [
                str(runtime.python),
                "-I",
                "-B",
                "-c",
                _WINDOW_PROBE,
                str(runtime.source_root),
                environment["HERMES_HOME"],
                route["provider"],
                route["model"],
                route["expectedHost"],
            ],
            cwd=environment["HERMES_HOME"],
            env={**environment, **runtime.environment},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if done.returncode:
            result["nativeExitCode"] = done.returncode
        else:
            observed = json.loads(done.stdout)
            if not isinstance(observed, dict):
                raise ValueError("native window diagnostic is not an object")
            result.update(
                {
                    k: observed[k]
                    for k in (
                        "status",
                        "resolvedWindow",
                        "windowSource",
                        "errorType",
                        "blockedEvents",
                        "scopeAdmission",
                    )
                    if k in observed
                }
            )
    except subprocess.TimeoutExpired:
        result["status"] = "native-window-timeout"
    except (OSError, ValueError) as error:
        result["errorType"] = type(error).__name__
    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
    write_object(receipt_path, result)
    if result["status"] != "resolved" or type(result.get("resolvedWindow")) is not int:
        raise AuthorityError(
            "binding-unavailable: Hermes window lookup "
            f"{result['status']}; limit=60s; elapsed={result['elapsedSeconds']}s"
        )
    return result
