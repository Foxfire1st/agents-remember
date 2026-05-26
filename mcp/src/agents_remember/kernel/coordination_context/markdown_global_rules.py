from __future__ import annotations

from agents_remember.kernel.coordination_context.paths import clean_scalar


def handle_global_rule_line(parser, indent: int, stripped: str) -> None:
    if try_enter_global_eligibility_section(parser, indent, stripped):
        return
    if try_select_global_rule_list(parser, indent, stripped):
        return
    if is_global_rule_list_item(parser, indent, stripped):
        parser.append_global_rule_value(clean_scalar(stripped[2:]))


def try_enter_global_eligibility_section(parser, indent: int, stripped: str) -> bool:
    if indent != 4 or stripped not in {"include:", "exclude:"}:
        return False
    parser.current_eligibility_section = "include" if stripped == "include:" else "exclude"
    parser.current_list = None
    parser.saw_global_path_rule = True
    return True


def try_select_global_rule_list(parser, indent: int, stripped: str) -> bool:
    if indent != 6 or stripped not in {"paths:", "fileTypes:"}:
        return False
    parser.current_list = parser.rule_list_name(stripped)
    parser.saw_global_path_rule = True
    return True


def is_global_rule_list_item(parser, indent: int, stripped: str) -> bool:
    return indent == 8 and stripped.startswith("- ") and parser.current_list is not None


def append_global_path_rule(parser) -> None:
    if not parser.saw_global_path_rule:
        return
    parser.settings.path_rules.append(
        {
            "path": "",
            "includes": parser.include_paths or ["*"],
            "excludes": parser.exclude_paths,
            "include_file_types": parser.include_file_types,
            "exclude_file_types": parser.exclude_file_types,
        }
    )
