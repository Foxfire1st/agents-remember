$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
py -3 (Join-Path $ScriptDir "render-starter.py") @args
