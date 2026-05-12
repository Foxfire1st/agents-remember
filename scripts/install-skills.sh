#!/usr/bin/env bash
set -euo pipefail

# Expose this agents-remember-md checkout's skills to a harness-specific
# skills directory without moving or copying the canonical skill sources.

usage() {
  cat <<'EOF'
Usage:
  install-skills.sh --install-root PATH [options]

Examples:
  # Recursive skill scanners such as Codex and Claude Code
  ./agents-remember-md/scripts/install-skills.sh \
    --install-root ./.agents/skills

  # Direct skill folder scanners such as Windsurf
  ./agents-remember-md/scripts/install-skills.sh \
    --install-root ./.windsurf/skills \
    --layout flat

  # User-wide skills folder
  ./agents-remember-md/scripts/install-skills.sh \
    --install-root ~/.agents/skills

Creates by default:
  <install-root>/agents-remember-md -> <agents-remember-md-checkout>/skills

With --layout flat:
  <install-root>/<skill-name> -> <canonical-skill-directory>

Options:
  --install-root PATH
      Destination skills directory where symlinks should be created.

  --layout tree|flat
      tree: create one namespace symlink to the canonical skills tree.
      flat: create one direct symlink per skill using the SKILL.md name.
      Defaults to tree.

  --source PATH
      Optional path to an agents-remember-md checkout or directly to its skills
      directory. Defaults to the checkout that owns this script.

  --archive-local-copies
      Move conflicting copied folders out of the install root into
      .archived-local-copies/<timestamp>/ before linking. Nothing is deleted.
EOF
}

archive_local_copies=false
install_root_arg=""
layout="tree"
source_arg=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root|--skills-dir)
      if [[ $# -lt 2 || "$2" == --* ]]; then
        echo "$1 requires a path argument." >&2
        usage >&2
        exit 2
      fi
      install_root_arg="$2"
      shift 2
      ;;
    --layout)
      if [[ $# -lt 2 || "$2" == --* ]]; then
        echo "--layout requires tree or flat." >&2
        usage >&2
        exit 2
      fi
      case "$2" in
        tree|namespace)
          layout="tree"
          ;;
        flat|direct)
          layout="flat"
          ;;
        *)
          echo "Unsupported layout: $2" >&2
          usage >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --source)
      if [[ $# -lt 2 || "$2" == --* ]]; then
        echo "--source requires a path argument." >&2
        usage >&2
        exit 2
      fi
      source_arg="$2"
      shift 2
      ;;
    --archive-local-copies)
      archive_local_copies=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
checkout_root="$(cd "$script_dir/.." && pwd -P)"

expand_home() {
  local path="$1"

  if [[ "$path" == "~" ]]; then
    printf '%s\n' "$HOME"
  elif [[ "$path" == "~/"* ]]; then
    printf '%s/%s\n' "$HOME" "${path#~/}"
  else
    printf '%s\n' "$path"
  fi
}

resolve_existing_dir() {
  local path="$1"

  if [[ ! -d "$path" ]]; then
    return 1
  fi

  (cd "$path" && pwd -P)
}

resolve_source_root() {
  local source="$1"

  if [[ -z "$source" ]]; then
    source="$checkout_root"
  else
    source="$(expand_home "$source")"
  fi

  if [[ -d "$source/skills" ]]; then
    resolve_existing_dir "$source/skills"
    return
  fi

  if [[ -d "$source" ]]; then
    resolve_existing_dir "$source"
    return
  fi

  return 1
}

read_skill_name() {
  local skill_file="$1"

  awk '
    { sub(/\r$/, "", $0) }
    NR == 1 && $0 == "---" { in_frontmatter = 1; next }
    in_frontmatter && $0 == "---" { exit }
    in_frontmatter && $0 ~ /^name:[[:space:]]*/ {
      sub(/^name:[[:space:]]*/, "", $0)
      gsub(/^[[:space:]]+/, "", $0)
      gsub(/[[:space:]]+$/, "", $0)
      print $0
      exit
    }
  ' "$skill_file"
}

archive_existing_path() {
  local path="$1"
  local name
  local archive_root
  local timestamp

  timestamp="$(date +%Y%m%d-%H%M%S)"
  archive_root="$install_root/.archived-local-copies/$timestamp"
  name="$(basename "$path")"
  mkdir -p "$archive_root"
  mv "$path" "$archive_root/"
  echo "Archived local copy: $path -> $archive_root/$name"
}

create_or_update_symlink() {
  local link_path="$1"
  local target="$2"
  local current_target

  if [[ "$archive_local_copies" == true && -e "$link_path" && ! -L "$link_path" ]]; then
    archive_existing_path "$link_path"
  fi

  if [[ -L "$link_path" ]]; then
    current_target="$(readlink "$link_path")"
    if [[ "$current_target" != "$target" ]]; then
      ln -sfn "$target" "$link_path"
      echo "Updated symlink: $link_path -> $target"
    else
      echo "Symlink already correct: $link_path -> $target"
    fi
  elif [[ -e "$link_path" ]]; then
    echo "Cannot create symlink because this path already exists and is not a symlink:"
    echo "  $link_path"
    echo "Move it away manually or rerun with --archive-local-copies if it is a local copy."
    exit 1
  else
    ln -s "$target" "$link_path"
    echo "Created symlink: $link_path -> $target"
  fi
}

archive_tree_local_copies() {
  local archive_root
  local name
  local path
  local timestamp

  timestamp="$(date +%Y%m%d-%H%M%S)"
  archive_root="$install_root/.archived-local-copies/$timestamp"
  mkdir -p "$archive_root"

  shopt -s nullglob
  for path in "$install_root"/*; do
    name="$(basename "$path")"
    case "$name" in
      agents-remember-md|install-skills.sh|sync-agents-remember-skills.sh)
        continue
        ;;
    esac

    if [[ -d "$path" && ! -L "$path" ]]; then
      mv "$path" "$archive_root/"
      echo "Archived local copy: $path -> $archive_root/$name"
    fi
  done
  shopt -u nullglob
}

install_tree_layout() {
  local link_path
  local skill_count

  link_path="$install_root/agents-remember-md"
  create_or_update_symlink "$link_path" "$source_root"

  if [[ "$archive_local_copies" == true ]]; then
    archive_tree_local_copies
  fi

  skill_count="$(find -L "$link_path" -name SKILL.md | wc -l | tr -d ' ')"
  echo "Visible canonical skills through namespace symlink: $skill_count"
}

install_flat_layout() {
  local duplicate_source
  local index
  local link_path
  local name
  local names_seen
  local skill_dir
  local skill_file
  local skill_names=()
  local skill_dirs=()

  names_seen=$'\n'

  while IFS= read -r -d '' skill_file; do
    skill_dir="$(cd "$(dirname "$skill_file")" && pwd -P)"
    name="$(read_skill_name "$skill_file")"

    if [[ -z "$name" ]]; then
      echo "Missing frontmatter name in: $skill_file" >&2
      exit 1
    fi

    if [[ ! "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
      echo "Unsupported skill name in $skill_file: $name" >&2
      echo "Flat layout requires lowercase letters, numbers, and hyphens." >&2
      exit 1
    fi

    if [[ "$names_seen" == *$'\n'"$name"$'\n'* ]]; then
      duplicate_source=""
      for index in "${!skill_names[@]}"; do
        if [[ "${skill_names[$index]}" == "$name" ]]; then
          duplicate_source="${skill_dirs[$index]}"
          break
        fi
      done
      echo "Duplicate skill name for flat layout: $name" >&2
      echo "  $duplicate_source" >&2
      echo "  $skill_dir" >&2
      exit 1
    fi

    names_seen+="$name"$'\n'
    skill_names+=("$name")
    skill_dirs+=("$skill_dir")
  done < <(find "$source_root" -name SKILL.md -print0)

  for index in "${!skill_names[@]}"; do
    name="${skill_names[$index]}"
    skill_dir="${skill_dirs[$index]}"
    link_path="$install_root/$name"
    create_or_update_symlink "$link_path" "$skill_dir"
  done

  echo "Visible canonical skills through direct symlinks: ${#skill_names[@]}"
}

if [[ -z "$install_root_arg" ]]; then
  echo "--install-root is required." >&2
  usage >&2
  exit 2
fi

if ! source_root="$(resolve_source_root "$source_arg")"; then
  echo "Canonical skill tree not found." >&2
  echo "Pass --source /path/to/agents-remember-md or --source /path/to/skills." >&2
  exit 1
fi

first_skill="$(find "$source_root" -name SKILL.md -print -quit)"
if [[ -z "$first_skill" ]]; then
  echo "No SKILL.md files found under: $source_root" >&2
  exit 1
fi

install_root_arg="$(expand_home "$install_root_arg")"
mkdir -p "$install_root_arg"
install_root="$(resolve_existing_dir "$install_root_arg")"

case "$layout" in
  tree)
    install_tree_layout
    ;;
  flat)
    install_flat_layout
    ;;
esac

echo "Done. Restart or reload the harness if its skill picker does not refresh."
