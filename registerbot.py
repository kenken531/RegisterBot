"""
RegisterBot  —  Windows Edition
=================================
A tiny CPU simulator: 8 registers, ALU, and a minimal instruction set.
MOV, ADD, SUB, MUL, CMP, JMP, JZ, JNZ, HALT.
A local LLM narrates each step of execution.

Prerequisites:
    1. Install ollama:        https://ollama.com/download
    2. Start ollama server:   ollama serve   (separate terminal)
    3. Pull a model:          ollama pull qwen2.5:3b
    4. Install Python deps:   pip install ollama rich

Usage:
    python registerbot.py                          # run built-in demo programs
    python registerbot.py --program fibonacci      # named program
    python registerbot.py --file myprogram.asm     # load from file
    python registerbot.py --model llama3.2:3b      # different model
    python registerbot.py --no-narrate             # skip LLM narration (fast mode)
"""

import argparse
import sys
import time
import re
from dataclasses import dataclass, field
from typing import Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.rule import Rule
    from rich.syntax import Syntax
    from rich.columns import Columns
    from rich import print as rprint
    from rich.live import Live
    from rich.layout import Layout
except ImportError:
    print("ERROR: rich not installed. Run: pip install rich")
    sys.exit(1)

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

# ── Console ───────────────────────────────────────────────────────────────────

console = Console()

# ── CPU Constants ─────────────────────────────────────────────────────────────

NUM_REGISTERS  = 8
MEMORY_SIZE    = 256          # bytes of RAM
MAX_STEPS      = 10_000       # safety: halt if too many steps (infinite loop guard)

# Register names
REG_NAMES = ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7"]

# Flags
FLAG_ZERO     = "Z"
FLAG_NEGATIVE = "N"
FLAG_OVERFLOW = "V"

# ── Instruction Set ───────────────────────────────────────────────────────────

OPCODES = {
    "MOV",    # MOV Rd, Rs|imm     — move/load
    "ADD",    # ADD Rd, Ra, Rb|imm — add
    "SUB",    # SUB Rd, Ra, Rb|imm — subtract
    "MUL",    # MUL Rd, Ra, Rb|imm — multiply
    "DIV",    # DIV Rd, Ra, Rb|imm — integer divide
    "AND",    # AND Rd, Ra, Rb|imm — bitwise AND
    "OR",     # OR  Rd, Ra, Rb|imm — bitwise OR
    "XOR",    # XOR Rd, Ra, Rb|imm — bitwise XOR
    "NOT",    # NOT Rd, Ra          — bitwise NOT
    "CMP",    # CMP Ra, Rb|imm     — compare (sets flags, no destination)
    "JMP",    # JMP label|addr     — unconditional jump
    "JZ",     # JZ  label|addr     — jump if Zero flag set
    "JNZ",    # JNZ label|addr     — jump if Zero flag NOT set
    "JN",     # JN  label|addr     — jump if Negative flag set
    "JP",     # JP  label|addr     — jump if Positive (not negative, not zero)
    "HALT",   # HALT               — stop execution
    "NOP",    # NOP                — no operation
    "PRINT",  # PRINT Ra|imm       — output value (debug aid)
    "LOAD",   # LOAD Rd, [addr]    — load from memory
    "STORE",  # STORE Ra, [addr]   — store to memory
}

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Instruction:
    opcode:  str
    args:    list
    label:   Optional[str] = None   # label defined on this line
    comment: str = ""
    line_no: int = 0
    raw:     str = ""


@dataclass
class CPUState:
    registers: list = field(default_factory=lambda: [0] * NUM_REGISTERS)
    memory:    list = field(default_factory=lambda: [0] * MEMORY_SIZE)
    pc:        int  = 0       # program counter
    flags:     dict = field(default_factory=lambda: {"Z": False, "N": False, "V": False})
    halted:    bool = False
    steps:     int  = 0
    output:    list = field(default_factory=list)    # PRINT output log

    def copy(self):
        return CPUState(
            registers=list(self.registers),
            memory=list(self.memory),
            pc=self.pc,
            flags=dict(self.flags),
            halted=self.halted,
            steps=self.steps,
            output=list(self.output),
        )

    def set_flags(self, value: int):
        self.flags["Z"] = (value == 0)
        self.flags["N"] = (value < 0)
        self.flags["V"] = (value > 2**31 - 1 or value < -(2**31))

# ── Assembler / Parser ────────────────────────────────────────────────────────

def parse_operand(token: str) -> tuple:
    """
    Parse a single operand token.
    Returns ("reg", index) for registers, ("imm", value) for immediates,
    ("label", name) for unresolved labels, ("mem", addr) for [addr].
    """
    token = token.strip()

    # Memory reference [addr] or [Rx]
    if token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        t, v  = parse_operand(inner)
        return ("mem", (t, v))

    # Register
    if token.upper() in REG_NAMES:
        return ("reg", REG_NAMES.index(token.upper()))

    # Hex immediate
    if token.startswith("0x") or token.startswith("0X"):
        return ("imm", int(token, 16))

    # Decimal immediate
    try:
        return ("imm", int(token))
    except ValueError:
        pass

    # Label reference
    return ("label", token)


def assemble(source: str) -> tuple:
    """
    Parse assembly source into (instructions, labels) where:
      instructions = list of Instruction
      labels = dict mapping label_name → instruction index
    """
    instructions = []
    labels       = {}

    for line_no, raw_line in enumerate(source.splitlines(), 1):
        # Strip comments
        comment = ""
        if ";" in raw_line:
            idx     = raw_line.index(";")
            comment = raw_line[idx + 1:].strip()
            line    = raw_line[:idx]
        else:
            line = raw_line

        line = line.strip()
        if not line:
            continue

        # Detect label definitions: "LABEL:" or "LABEL: instruction"
        label_name = None
        label_match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)', line)
        if label_match:
            label_name = label_match.group(1).upper()
            line       = label_match.group(2).strip()

        # Register label → points to NEXT instruction index
        if label_name:
            labels[label_name] = len(instructions)

        if not line:
            # Label-only line (no instruction on same line)
            continue

        # Tokenise
        tokens = [t.strip() for t in re.split(r'[\s,]+', line) if t.strip()]
        if not tokens:
            continue

        opcode = tokens[0].upper()
        if opcode not in OPCODES:
            raise SyntaxError(
                f"Line {line_no}: Unknown opcode '{opcode}' in: {raw_line.strip()}"
            )

        args = [parse_operand(t) for t in tokens[1:]]

        instr = Instruction(
            opcode  = opcode,
            args    = args,
            label   = label_name,
            comment = comment,
            line_no = line_no,
            raw     = raw_line.strip(),
        )
        instructions.append(instr)

    # Second pass: resolve label references in args
    for instr in instructions:
        resolved = []
        for kind, val in instr.args:
            if kind == "label":
                key = val.upper()
                if key not in labels:
                    raise NameError(
                        f"Undefined label '{val}' referenced near line {instr.line_no}"
                    )
                resolved.append(("imm", labels[key]))
            elif kind == "mem" and val[0] == "label":
                key = val[1].upper()
                if key not in labels:
                    raise NameError(
                        f"Undefined label '{val[1]}' in memory reference near line {instr.line_no}"
                    )
                resolved.append(("mem", ("imm", labels[key])))
            else:
                resolved.append((kind, val))
        instr.args = resolved

    return instructions, labels

# ── CPU / ALU ─────────────────────────────────────────────────────────────────

class CPU:
    """
    Fetch-Decode-Execute cycle implementation.
    Each call to step() executes one instruction and returns an ExecutionResult.
    """

    def __init__(self, instructions: list, labels: dict):
        self.instructions = instructions
        self.labels       = labels
        self.state        = CPUState()

    def _resolve(self, state: CPUState, arg: tuple) -> int:
        """Resolve an operand to its integer value."""
        kind, val = arg
        if kind == "imm":
            return val
        elif kind == "reg":
            return state.registers[val]
        elif kind == "mem":
            addr = self._resolve(state, val)
            addr = addr % MEMORY_SIZE
            return state.memory[addr]
        else:
            raise ValueError(f"Cannot resolve operand ({kind}, {val})")

    def _reg_idx(self, arg: tuple) -> int:
        """Get register index from a reg operand."""
        kind, val = arg
        if kind != "reg":
            raise TypeError(f"Expected register, got ({kind}, {val})")
        return val

    def step(self) -> dict:
        """
        Execute one instruction.
        Returns a result dict describing what happened.
        """
        state = self.state

        if state.halted:
            return {"ok": False, "reason": "CPU is halted", "changed_regs": []}

        if state.pc >= len(self.instructions):
            state.halted = True
            return {"ok": False, "reason": "PC out of bounds — implicit HALT",
                    "changed_regs": []}

        if state.steps >= MAX_STEPS:
            state.halted = True
            return {"ok": False, "reason": f"Step limit ({MAX_STEPS}) reached — possible infinite loop",
                    "changed_regs": []}

        instr = self.instructions[state.pc]
        state.steps += 1
        prev_regs = list(state.registers)
        prev_pc   = state.pc
        jumped    = False

        # ── Execute ──────────────────────────────────────────────────────────

        op = instr.opcode

        if op == "NOP":
            pass

        elif op == "HALT":
            state.halted = True

        elif op == "MOV":
            rd  = self._reg_idx(instr.args[0])
            val = self._resolve(state, instr.args[1])
            state.registers[rd] = val
            state.set_flags(val)

        elif op in ("ADD", "SUB", "MUL", "DIV", "AND", "OR", "XOR"):
            rd  = self._reg_idx(instr.args[0])
            a   = self._resolve(state, instr.args[1])
            b   = self._resolve(state, instr.args[2])
            if   op == "ADD": result = a + b
            elif op == "SUB": result = a - b
            elif op == "MUL": result = a * b
            elif op == "DIV":
                if b == 0:
                    raise ZeroDivisionError(f"Division by zero at PC={state.pc}")
                result = int(a / b)
            elif op == "AND": result = a & b
            elif op == "OR":  result = a | b
            elif op == "XOR": result = a ^ b
            state.registers[rd] = result
            state.set_flags(result)

        elif op == "NOT":
            rd     = self._reg_idx(instr.args[0])
            a      = self._resolve(state, instr.args[1])
            result = ~a
            state.registers[rd] = result
            state.set_flags(result)

        elif op == "CMP":
            a      = self._resolve(state, instr.args[0])
            b      = self._resolve(state, instr.args[1])
            result = a - b
            state.set_flags(result)

        elif op == "JMP":
            addr      = self._resolve(state, instr.args[0])
            state.pc  = addr
            jumped    = True

        elif op == "JZ":
            addr = self._resolve(state, instr.args[0])
            if state.flags["Z"]:
                state.pc = addr
                jumped   = True

        elif op == "JNZ":
            addr = self._resolve(state, instr.args[0])
            if not state.flags["Z"]:
                state.pc = addr
                jumped   = True

        elif op == "JN":
            addr = self._resolve(state, instr.args[0])
            if state.flags["N"]:
                state.pc = addr
                jumped   = True

        elif op == "JP":
            addr = self._resolve(state, instr.args[0])
            if not state.flags["N"] and not state.flags["Z"]:
                state.pc = addr
                jumped   = True

        elif op == "PRINT":
            val = self._resolve(state, instr.args[0])
            state.output.append(str(val))

        elif op == "LOAD":
            rd   = self._reg_idx(instr.args[0])
            addr = self._resolve(state, instr.args[1]) % MEMORY_SIZE
            state.registers[rd] = state.memory[addr]
            state.set_flags(state.registers[rd])

        elif op == "STORE":
            val  = self._resolve(state, instr.args[0])
            addr = self._resolve(state, instr.args[1]) % MEMORY_SIZE
            state.memory[addr] = val

        # Advance PC (unless we jumped)
        if not jumped and not state.halted:
            state.pc += 1

        # Find changed registers
        changed_regs = [i for i in range(NUM_REGISTERS)
                        if state.registers[i] != prev_regs[i]]

        return {
            "ok":           True,
            "instr":        instr,
            "prev_pc":      prev_pc,
            "jumped":       jumped,
            "changed_regs": changed_regs,
            "flags":        dict(state.flags),
        }

# ── LLM Narration ─────────────────────────────────────────────────────────────

NARRATE_SYSTEM = (
    "You are a CPU architecture tutor narrating a register machine simulation step by step. "
    "When given an instruction and register state, explain in ONE short sentence (max 15 words) "
    "what the CPU is doing and why. Be specific with register names and values. "
    "Use plain English, no markdown."
)

def narrate_step(model: str, instr: Instruction, state: CPUState,
                 result: dict) -> str:
    """Ask the LLM to narrate a single instruction execution."""
    reg_str = ", ".join(
        f"R{i}={state.registers[i]}"
        for i in range(NUM_REGISTERS)
        if state.registers[i] != 0 or i < 4
    )
    flags_str = " ".join(
        f"{k}={'1' if v else '0'}" for k, v in state.flags.items()
    )
    prompt = (
        f"Instruction: {instr.raw}\n"
        f"PC was: {result['prev_pc']}  →  now: {state.pc}\n"
        f"Registers: {reg_str}\n"
        f"Flags: {flags_str}\n"
        f"Changed registers: {[f'R{i}' for i in result['changed_regs']]}\n"
        f"Jumped: {result['jumped']}\n"
        "Narrate this step in ONE sentence."
    )
    try:
        resp = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": NARRATE_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            stream=False,
        )
        return resp["message"]["content"].strip().splitlines()[0]
    except Exception as e:
        return f"[narration unavailable: {e}]"

# ── Rich UI ───────────────────────────────────────────────────────────────────

def make_register_table(state: CPUState, changed: list) -> Table:
    """Render register file as a rich table."""
    table = Table(
        title="Register File",
        title_style="bold cyan",
        show_header=True,
        header_style="dim",
        border_style="dim",
        box=None,
        pad_edge=False,
    )
    table.add_column("Reg",   style="cyan bold",   width=5)
    table.add_column("Value", style="white",        width=12, justify="right")
    table.add_column("Hex",   style="dim",          width=10, justify="right")
    table.add_column("Bin",   style="dim",          width=12, justify="right")

    for i, val in enumerate(state.registers):
        name     = REG_NAMES[i]
        style    = "bold yellow on dark_orange3" if i in changed else ""
        val_clamped = val & 0xFFFFFFFF   # show as 32-bit unsigned for hex/bin
        table.add_row(
            Text(name, style=style or "cyan bold"),
            Text(str(val), style=style or "white"),
            Text(f"0x{val_clamped:08X}", style=style or "dim"),
            Text(f"{val_clamped:032b}"[-12:], style=style or "dim"),
        )

    return table


def make_flags_panel(state: CPUState) -> Panel:
    text = Text()
    for flag, val in state.flags.items():
        color = "green" if val else "red"
        text.append(f" {flag}=", style="dim")
        text.append("1" if val else "0", style=f"bold {color}")
        text.append("  ")
    return Panel(text, title="[dim]Flags[/dim]", border_style="dim",
                 padding=(0, 1))


def make_program_panel(instructions: list, pc: int, labels: dict) -> Panel:
    """Show the program listing with the current PC highlighted."""
    label_by_idx = {v: k for k, v in labels.items()}
    lines        = Text()

    for idx, instr in enumerate(instructions):
        is_current = (idx == pc)
        prefix     = "▶ " if is_current else "  "
        lbl        = label_by_idx.get(idx, "")
        lbl_str    = f"{lbl}:" if lbl else ""

        line = f"{idx:3d}  {lbl_str:<10} {instr.raw}"
        if instr.comment:
            line += f"  ; {instr.comment}"

        if is_current:
            lines.append(f"{prefix}{line}\n", style="bold yellow on grey15")
        else:
            style = "dim" if idx < pc else "white"
            lines.append(f"{prefix}{line}\n", style=style)

    return Panel(
        lines,
        title="[cyan]Program[/cyan]",
        border_style="cyan",
        padding=(0, 1),
    )


def make_output_panel(state: CPUState) -> Panel:
    content = "\n".join(state.output[-10:]) if state.output else "[dim]No output yet[/dim]"
    return Panel(content, title="[green]PRINT Output[/green]",
                 border_style="green", padding=(0, 1))


def display_step(instructions: list, labels: dict, state: CPUState,
                 result: dict, narration: str, step_num: int):
    """Print one full step of the simulation."""
    instr = result.get("instr")

    console.print()
    console.print(Rule(
        f"[bold]Step {step_num}[/bold]  PC={result['prev_pc']}  "
        f"[cyan]{instr.raw if instr else 'HALT'}[/cyan]",
        style="dim"
    ))

    # Side-by-side: program + registers
    prog_panel = make_program_panel(instructions, state.pc, labels)
    reg_table  = make_register_table(state, result["changed_regs"])
    flags_panel= make_flags_panel(state)

    console.print(Columns([prog_panel, reg_table], equal=False, expand=True))
    console.print(flags_panel)

    # Output log
    if state.output:
        console.print(make_output_panel(state))

    # Narration
    if narration:
        console.print(Panel(
            Text(narration, style="italic"),
            title="[magenta]🤖 CPU Narrator[/magenta]",
            border_style="magenta",
            padding=(0, 2),
        ))

    # Status
    if state.halted:
        console.print(Panel(
            f"[bold green]✓ Execution complete in {state.steps} step(s)[/bold green]",
            border_style="green",
        ))


def display_final_summary(state: CPUState, program_name: str):
    console.print()
    console.print(Rule("[bold cyan]Final State[/bold cyan]", style="cyan"))

    table = Table(show_header=False, border_style="dim", box=None)
    table.add_column("", style="dim",   width=18)
    table.add_column("", style="white")

    table.add_row("Total steps",   str(state.steps))
    table.add_row("Final PC",      str(state.pc))
    table.add_row("PRINT output",  ", ".join(state.output) or "none")
    table.add_row("Flags",
                  " ".join(f"{k}={'1' if v else '0'}"
                           for k, v in state.flags.items()))
    console.print(table)

    console.print()
    reg_table = make_register_table(state, [])
    console.print(reg_table)

# ── Built-in programs ─────────────────────────────────────────────────────────

PROGRAMS = {

"demo": (
    "Basic demo",
"""; RegisterBot demo program
; Loads values, adds, subtracts, multiplies, then halts
    MOV R0, 10        ; load 10 into R0
    MOV R1, 20        ; load 20 into R1
    ADD R2, R0, R1    ; R2 = R0 + R1 = 30
    SUB R3, R1, R0    ; R3 = R1 - R0 = 10
    MUL R4, R2, R3    ; R4 = R2 * R3 = 300
    PRINT R4          ; output 300
    HALT              ; stop
"""),

"countdown": (
    "Countdown from 5 to 0",
"""; Countdown: counts R0 from 5 down to 0 using a loop
    MOV R0, 5         ; counter = 5
    MOV R1, 1         ; decrement amount
LOOP:
    PRINT R0          ; print current value
    SUB R0, R0, R1    ; counter -= 1
    CMP R0, 0         ; compare counter to 0
    JNZ LOOP          ; if not zero, loop
    PRINT R0          ; print final 0
    HALT
"""),

"fibonacci": (
    "Fibonacci sequence (first 8 terms)",
"""; Fibonacci: compute first 8 terms in R0, R1
; F(0)=0, F(1)=1, F(n)=F(n-1)+F(n-2)
    MOV R0, 0         ; F(n-2)
    MOV R1, 1         ; F(n-1)
    MOV R2, 8         ; how many terms to compute
    MOV R3, 0         ; iteration counter
    MOV R5, 1         ; constant 1
    PRINT R0          ; print F(0)
    PRINT R1          ; print F(1)
    MOV R3, 2         ; start from F(2)
FLOOP:
    ADD R4, R0, R1    ; R4 = F(n) = F(n-1) + F(n-2)
    PRINT R4          ; print F(n)
    MOV R0, R1        ; slide window: F(n-2) = F(n-1)
    MOV R1, R4        ; F(n-1) = F(n)
    ADD R3, R3, R5    ; counter++
    CMP R3, R2        ; compare counter to 8
    JNZ FLOOP         ; loop if not done
    HALT
"""),

"factorial": (
    "Factorial of 5 (5! = 120)",
"""; Factorial: compute 5! = 120
    MOV R0, 5         ; n = 5
    MOV R1, 1         ; accumulator = 1
    MOV R2, 1         ; constant 1
FACT:
    MUL R1, R1, R0    ; acc *= n
    SUB R0, R0, R2    ; n -= 1
    CMP R0, 0         ; is n == 0?
    JNZ FACT          ; if not, loop
    PRINT R1          ; print result (120)
    HALT
"""),

"max": (
    "Find maximum of two values",
"""; Find max of R0 and R1, store in R2
    MOV R0, 42        ; first value
    MOV R1, 17        ; second value
    CMP R0, R1        ; compare
    JN  SECOND        ; if R0 < R1, jump
    MOV R2, R0        ; R0 is max
    JMP DONE
SECOND:
    MOV R2, R1        ; R1 is max
DONE:
    PRINT R2          ; print max value
    HALT
"""),

}

# ── Main ──────────────────────────────────────────────────────────────────────

def run_simulation(source: str, program_name: str, model: str,
                   narrate: bool, delay: float):
    """Assemble and run the program, displaying each step."""

    # Assemble
    try:
        instructions, labels = assemble(source)
    except (SyntaxError, NameError) as e:
        console.print(f"[bold red]Assembly error:[/bold red] {e}")
        sys.exit(1)

    if not instructions:
        console.print("[yellow]No instructions to execute.[/yellow]")
        return

    cpu = CPU(instructions, labels)

    # Header
    console.print()
    console.print(Rule("[bold cyan]RegisterBot CPU Simulator[/bold cyan]",
                       style="cyan"))
    console.print(
        f"  [dim]Program:[/dim] [bold]{program_name}[/bold]  "
        f"[dim]|[/dim]  [dim]Instructions:[/dim] [bold]{len(instructions)}[/bold]  "
        f"[dim]|[/dim]  [dim]Labels:[/dim] [bold]{len(labels)}[/bold]  "
        f"[dim]|[/dim]  [dim]Model:[/dim] [bold]{model if narrate else 'off'}[/bold]"
    )
    console.print(Rule(style="dim"))

    # Show source
    console.print()
    console.print(Syntax(source.strip(), "asm",
                         theme="monokai", line_numbers=True,
                         background_color="default"))
    console.print()

    input(f"  [dim]Press ENTER to start execution...[/dim]  ")

    step_num = 0
    while not cpu.state.halted:
        result = cpu.step()

        if not result["ok"]:
            if "reason" in result:
                console.print(f"\n[dim]{result['reason']}[/dim]")
            break

        step_num += 1

        # Get narration
        narration = ""
        if narrate and HAS_OLLAMA and result.get("instr"):
            narration = narrate_step(model, result["instr"], cpu.state, result)

        display_step(instructions, labels, cpu.state, result,
                     narration, step_num)

        if delay > 0 and not cpu.state.halted:
            time.sleep(delay)

        if not cpu.state.halted:
            try:
                input(f"\n  [dim]Press ENTER for next step (or Ctrl+C to stop)...[/dim]  ")
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
                break

    display_final_summary(cpu.state, program_name)


def main():
    parser = argparse.ArgumentParser(
        description="RegisterBot — CPU simulator with LLM narration"
    )
    parser.add_argument("--program", "-p",
                        choices=list(PROGRAMS.keys()),
                        default="demo",
                        help="Built-in program to run (default: demo)")
    parser.add_argument("--file", "-f",
                        default=None,
                        help="Path to a .asm file to load and run")
    parser.add_argument("--model", "-m",
                        default="qwen2.5:3b",
                        help="Ollama model for narration (default: qwen2.5:3b)")
    parser.add_argument("--no-narrate",
                        action="store_true",
                        help="Disable LLM narration (faster, no ollama needed)")
    parser.add_argument("--delay",
                        type=float, default=0.0,
                        help="Auto-step delay in seconds (0 = manual step)")
    parser.add_argument("--list",
                        action="store_true",
                        help="List all built-in programs and exit")
    args = parser.parse_args()

    # List programs
    if args.list:
        console.print("\n[bold cyan]Built-in programs:[/bold cyan]\n")
        for name, (desc, _) in PROGRAMS.items():
            console.print(f"  [cyan]{name:<15}[/cyan]  {desc}")
        console.print()
        return

    narrate = not args.no_narrate and HAS_OLLAMA

    # Verify ollama if narrating
    if narrate:
        try:
            ollama.list()
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Cannot reach ollama — "
                          f"narration disabled. ({e})")
            console.print(f"  Run [dim]ollama serve[/dim] in a separate terminal "
                          f"to enable narration.\n")
            narrate = False

    # Load source
    if args.file:
        try:
            source       = open(args.file, encoding="utf-8").read()
            program_name = args.file
        except FileNotFoundError:
            console.print(f"[red]File not found:[/red] {args.file}")
            sys.exit(1)
    else:
        program_name = args.program
        _, source    = PROGRAMS[args.program]

    run_simulation(source, program_name, args.model, narrate, args.delay)


if __name__ == "__main__":
    main()