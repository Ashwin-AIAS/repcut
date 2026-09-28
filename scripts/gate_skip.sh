# Sourced by every verify_NN.sh that can skip. POSIX sh, no shebang: never run.
#
# The one place a gate decides whether a criterion may SKIP (amendment 014).
# A skip is allowed only when the check names one of the enumerated
# environment conditions AND scripts/gate_conditions.py, asked independently,
# finds that condition holds here. Anything else is a FAIL. In strict mode,
# which /gate runs, every skip is a FAIL: the gate machine has a console, the
# guide and a GPU, so every criterion must actually execute there.
#
# Strict mode is REPCUT_GATE_STRICT=1. It can only make a gate harder to pass,
# so it is not the loophole an allow-list variable would be; nested gates
# inherit it through the environment.
#
# Needs from the sourcing gate: $PY, ok(), no(), skipped(), scrub().

GATE_CONDITIONS="NO_CONSOLE NO_GUIDE NO_GPU"

gate_strict() { [ "${REPCUT_GATE_STRICT:-0}" = 1 ]; }

# gate_skip <label> <reason>   reason = "<CONDITION> <why>"
gate_skip() {
  _label="$1"
  _reason="$2"
  _condition="${_reason%% *}"
  case " $GATE_CONDITIONS " in
    *" $_condition "*) ;;
    *)
      no "$_label" "(a skip must name NO_CONSOLE, NO_GUIDE or NO_GPU: ${_reason:-no reason reported})"
      return
      ;;
  esac
  _evidence="$("$PY" scripts/gate_conditions.py "$_condition" 2>&1)"
  _holds=$?
  _evidence="$(printf '%s\n' "$_evidence" | tail -1 | scrub)"
  if [ "$_holds" != 0 ]; then
    no "$_label" "(claimed $_condition, but it does not hold here: $_evidence)"
    return
  fi
  if gate_strict; then
    no "$_label" "(strict: $_condition is not allowed to skip - $_evidence)"
    return
  fi
  skipped "$_label" "($_condition: $_evidence)"
}

# A nested gate's SKIP lines, so a skip three gates down is still on screen.
gate_nested_skips() {
  printf '%s\n' "$1" | grep -E '^[[:space:]]*\[SKIP\]' | sed 's/^/     /'
}

# The summary suffix: how many skipped, and in which mode.
gate_summary_note() {
  _note=""
  [ "$skip" -gt 0 ] && _note=" ($skip skipped, conditions printed above)"
  gate_strict && _note="$_note [strict]"
  printf '%s' "$_note"
}
