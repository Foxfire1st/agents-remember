# Bind command-line work to the serving build

Curators and other admitted command-line users read this topic before a deciding run or write. It supplies launch-interpreter binding and identity comparison; [Orientation](../../operations/orientation.md#bind-curation-to-the-serving-build) owns serving-build authority.

Prefer the mounted `knowledge_write` tool. Before a command-line interface (CLI) run that decides or writes, bind it to the starting server's `server_info.servingBuild`.

## Obtain the admitted interpreter

Obtain the original interpreter from `toolServer.command[0]` in the role-launch receipt, or from `command` in the selected server's startup definition. Preserve that spelling. The resolved `servingBuild.pythonExecutable` may follow a virtual-environment symbolic link to an interpreter without its dependencies.

If the admitted launch command is unavailable, report that the CLI binding is unavailable. Do not infer it from the resolved path or install another build.

Fill these values from the launch and starting `server_info`, then print the CLI identity:

```sh
AR_SERVING_PYTHON='<original admitted launch interpreter>'
AR_SERVING_PACKAGE='<server_info.servingBuild.packageRoot>'
AR_SERVING_PARENT="$(dirname -- "$AR_SERVING_PACKAGE")"
PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -c \
  'import json; from agents_remember.serving.build_info import process_serving_build; print(json.dumps(process_serving_build().payload().model_dump(mode="json", exclude_none=True)))'
```

## Compare the identity and invoke commands

Compare `version`, `sourceDigest`, `packageRoot`, `pythonExecutable` and `commit` with the starting stamp before using this CLI to decide or write. A missing identity or mismatch does not establish the serving build. Report it and use the mounted tool when that tool admits the operation. Keep an unreported commit unknown; never call it a verified clean checkout.

Use the same interpreter and explicit package path for public CLI commands:

```sh
PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli \
  knowledge-worklist --contract '<this leaf enclosure contract>'
PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli \
  knowledge-ingest --contract '<this leaf enclosure contract>' \
  --list '<the hand-off list>' --authorization-ref '<the admitted authorization>' --commit --json
```
