"""Shell snippets that keep the terminal title useful to ``pytime auto``.

The watcher only sees window titles, and a terminal's title is whatever the
shell last set. These hooks set it to ``<command> @ <project dir>`` while a
command runs and ``<project dir>`` at the prompt, where the project dir is
the git repository root (or the current directory outside a repository).
Only the command's first word is put in the title -- plus the file name for
terminal editors -- so arguments that might be secrets are never stored.
"""

from __future__ import annotations

SHELLS = ("bash", "zsh")

_COMMON = r"""# pytime auto: keep the terminal title as "<command> @ <project dir>" so
# `pytime auto watch` can attribute terminal time to a project.
#   add to your shell rc file:  eval "$(pytime auto shell-init {shell})"
# Claude Code would otherwise replace the title with its conversation topic.
export CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1
__pytime_dir() {{
  local d
  d=$(git rev-parse --show-toplevel 2>/dev/null) || d=$PWD
  case $d in "$HOME"*) d="~${{d#"$HOME"}}" ;; esac
  printf '%s' "$d"
}}
# The directory is looked up once per prompt, and a title identical to the
# last one isn't rewritten, so a loop typed at the prompt stays cheap.
__pytime_precmd() {{
  __pytime_cwd=$(__pytime_dir)
  __pytime_last=$__pytime_cwd
  __pytime_armed=1
  printf '\033]0;%s\007' "$__pytime_cwd"
}}
__pytime_preexec() {{
  local cmd=$1 first last title
  # Skip leading VAR=value words and wrappers, so "sudo vim x" is "vim x".
  while :; do
    first=${{cmd%%[[:space:]]*}}
    case $first in
      *=*|sudo|env|nohup|time|command|exec|nice|npx|bunx|pnpx)
        [ "$first" = "$cmd" ] && break
        cmd=${{cmd#"$first"}}
        cmd=${{cmd#"${{cmd%%[![:space:]]*}}"}}
        ;;
      *) break ;;
    esac
  done
  last=${{cmd##*[[:space:]]}}
  case $first in
    vim|nvim|vi|nano|emacs|hx|helix|micro|kak) [ "$last" != "$first" ] && first="$first $last" ;;
  esac
  title="$first @ ${{__pytime_cwd:-$(__pytime_dir)}}"
  [ "$title" = "${{__pytime_last-}}" ] && return
  __pytime_last=$title
  printf '\033]0;%s\007' "$title"
}}
"""

_BASH = r"""# Ubuntu's default prompt sets its own title on every prompt; drop that part.
PS1=${PS1//'\[\e]0;\u@\h: \w\a\]'/}
if declare -p preexec_functions >/dev/null 2>&1; then
  # bash-preexec is loaded: use its hooks instead of taking over the DEBUG trap.
  preexec_functions+=(__pytime_preexec)
  precmd_functions+=(__pytime_precmd)
else
  # Note: this replaces any DEBUG trap you already had. It acts on the first
  # command of each line only, like zsh's preexec.
  __pytime_debug() {
    [[ -n $COMP_LINE || -z ${__pytime_armed-} || $BASH_COMMAND == __pytime_* ]] && return
    __pytime_armed=
    __pytime_preexec "$BASH_COMMAND"
  }
  trap '__pytime_debug' DEBUG
  PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND; }__pytime_precmd"
fi
"""

_ZSH = r"""# oh-my-zsh: don't overwrite the title we set.
DISABLE_AUTO_TITLE=true
autoload -Uz add-zsh-hook
add-zsh-hook precmd __pytime_precmd
add-zsh-hook preexec __pytime_preexec
"""


def snippet(shell: str) -> str:
    """The hook for ``shell`` (``bash`` or ``zsh``)."""
    tail = {"bash": _BASH, "zsh": _ZSH}[shell]
    return _COMMON.format(shell=shell) + tail
