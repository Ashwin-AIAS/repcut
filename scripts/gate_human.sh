# Sourced by every verify_NN.sh. POSIX sh, no shebang: never run.
#
# The one place a gate decides a [HUMAN] criterion (amendment 016), and how a
# gate finishes. Two modes, chosen by the gate's own argument:
#
#   verify_NN.sh          the gate. Every [HUMAN] box must be ticked by Ashwin;
#                         an unticked box is a FAIL. Never PASS, never SKIP.
#   verify_NN.sh --auto   everything a machine can check. A ticked [HUMAN]
#                         criterion still PASSes; an unticked one is printed as
#                         [HUMAN] pending and counted apart, never as a PASS.
#                         Exit 0 here says nothing about the gate itself.
#
# The mode comes from the argument, never from the environment: a plain run is
# the full gate whatever the shell exports, so no variable can turn an
# unsigned box green (amendment 014's reasoning against override switches).
# A gate passes its own mode to the gate it nests via $(gate_mode_arg).
#
# Needs from the sourcing gate: ok(), no(), and the counters pass/fail/skip.

GATE_AUTO=0
human=0

# gate_mode "$@" - the first thing a gate does after sourcing this file.
gate_mode() {
  if [ "$#" -gt 1 ]; then
    echo "usage: $0 [--auto]"; exit 1
  fi
  case "${1:-}" in
    "") GATE_AUTO=0 ;;
    --auto) GATE_AUTO=1 ;;
    *) echo "usage: $0 [--auto]  (unknown argument: $1)"; exit 1 ;;
  esac
}

gate_auto() { [ "$GATE_AUTO" = 1 ]; }

# The argument a nested gate is run with, so -auto nests as -auto.
gate_mode_arg() { if gate_auto; then printf '%s' "--auto"; fi; }

human_pending() {
  printf "  [HUMAN] %-45s %s\n" "$1" "${2:-}"
  human=$((human+1))
}

# gate_human <label> <manual-check file> [<heading prefix>]
# Counts the checkboxes in the file, or only under the "### <prefix>..." heading.
gate_human() {
  _label="$1"
  _file="$2"
  _section="${3:-}"
  if [ ! -f "$_file" ]; then
    # A missing checklist is a defect, not a pending signature, in either mode.
    no "$_label" "($_file does not exist)"
    return
  fi
  if [ -n "$_section" ]; then
    _boxes="$(awk -v h="### $_section" 'index($0, h) == 1 {on=1; next} /^### /{on=0} on' "$_file")"
  else
    _boxes="$(cat "$_file")"
  fi
  _unticked="$(printf '%s\n' "$_boxes" | grep -cE '^[[:space:]]*-[[:space:]]*\[[[:space:]]\]')"
  _ticked="$(printf '%s\n' "$_boxes" | grep -cE '^[[:space:]]*-[[:space:]]*\[[xX]\]')"
  if [ "${_unticked:-1}" = 0 ] && [ "${_ticked:-0}" -gt 0 ]; then
    ok "$_label" "($_ticked of $_ticked ticked)"
    return
  fi
  if gate_auto; then
    human_pending "$_label" "($_unticked unticked: pending Ashwin, $_file)"
    return
  fi
  no "$_label" "($_unticked unticked, $_ticked ticked)"
  printf "         [HUMAN] unsigned: %s\n" "$_file"
}

# gate_stop_bound <label> <rc> <detail> - an automated measurement whose verdict
# can depend on a decision deferred to a human stop (amendment 017, PROPOSED).
# The check exits 3 only when every failure it found is bound to that decision;
# any other failure exits 1 and is a FAIL in both modes. In the gate, 3 is a
# FAIL too. In -auto it is pending, printed with its measurement, never a PASS.
gate_stop_bound() {
  if [ "$2" = 0 ]; then
    ok "$1" "$3"
  elif [ "$2" = 3 ] && gate_auto; then
    human_pending "$1" "(bound to a human stop) $3"
  else
    no "$1" "$3"
  fi
}

# gate_finish <gate name> - the summary line and the exit code.
gate_finish() {
  _note="$(gate_summary_note)"
  if gate_auto; then
    _hnote=""
    [ "$human" -gt 0 ] && _hnote="; $human [HUMAN] pending, so $1 itself is still red"
    if [ "$fail" -eq 0 ]; then
      echo "PASSED: $pass of $((pass+fail)) criteria [auto$_hnote]$_note"; exit 0
    fi
    echo "FAILED: $fail of $((pass+fail)) criteria [auto$_hnote]$_note"; exit 1
  fi
  if [ "$fail" -eq 0 ]; then
    echo "PASSED: $pass of $((pass+fail)) criteria$_note"; exit 0
  fi
  echo "FAILED: $fail of $((pass+fail)) criteria$_note"; exit 1
}
