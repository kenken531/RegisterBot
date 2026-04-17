# RegisterBot 🤖

RegisterBot is a **tiny CPU simulator built from scratch in pure Python**: 8 registers, an ALU, and a minimal instruction set — MOV, ADD, SUB, MUL, DIV, CMP, JMP, JZ, JNZ, HALT, and more. Feed it assembly-like programs and a local LLM narrates every single step of execution. It's built for the **BUILDCORED ORCAS — Day 14** challenge.

## How it works

- A **two-pass assembler** parses assembly source into instructions, resolves label references, and supports hex/decimal/negative immediates, register operands, and memory references.
- The **CPU class** implements the full fetch-decode-execute cycle: each call to `step()` fetches the current instruction at the program counter, decodes its operands, executes the ALU or control operation, updates registers and flags, and advances the PC.
- **Three flags** are maintained after every operation: `Z` (zero), `N` (negative), `V` (overflow). Conditional jumps (`JZ`, `JNZ`, `JN`, `JP`) read these flags.
- A **10,000 step guard** catches infinite loops and halts automatically.
- At each step, **rich** renders a live view of the register file (with changed registers highlighted), the program listing with the current PC highlighted, and the flags panel.
- If ollama is running, a **local LLM narrates each instruction** in one plain-English sentence — explaining what the CPU did and why.

## Instruction set

| Instruction | Syntax | Description |
|---|---|---|
| `MOV` | `MOV Rd, Rs\|imm` | Load immediate or copy register |
| `ADD` | `ADD Rd, Ra, Rb\|imm` | Integer addition |
| `SUB` | `SUB Rd, Ra, Rb\|imm` | Integer subtraction |
| `MUL` | `MUL Rd, Ra, Rb\|imm` | Integer multiplication |
| `DIV` | `DIV Rd, Ra, Rb\|imm` | Integer division |
| `AND` | `AND Rd, Ra, Rb\|imm` | Bitwise AND |
| `OR` | `OR Rd, Ra, Rb\|imm` | Bitwise OR |
| `XOR` | `XOR Rd, Ra, Rb\|imm` | Bitwise XOR |
| `NOT` | `NOT Rd, Ra` | Bitwise NOT |
| `CMP` | `CMP Ra, Rb\|imm` | Compare (sets flags, no destination) |
| `JMP` | `JMP label` | Unconditional jump |
| `JZ` | `JZ label` | Jump if Zero flag set |
| `JNZ` | `JNZ label` | Jump if Zero flag NOT set |
| `JN` | `JN label` | Jump if Negative flag set |
| `JP` | `JP label` | Jump if Positive |
| `PRINT` | `PRINT Ra\|imm` | Output a value to the terminal |
| `LOAD` | `LOAD Rd, [addr]` | Load from memory |
| `STORE` | `STORE Ra, [addr]` | Store to memory |
| `NOP` | `NOP` | No operation |
| `HALT` | `HALT` | Stop execution |

## Requirements

- Python 3.10.x
- [ollama](https://ollama.com/download) installed and running (optional — narration only)

## Python packages:

```bash
pip install ollama rich
```

## Setup

1. Download and install ollama from [ollama.com/download](https://ollama.com/download).
2. In a **separate terminal**, start the ollama server:
```
ollama serve
```
3. Pull the model:
```
ollama pull qwen2.5:3b
```
4. Install Python packages:
```
pip install -r requirements.txt
```

## Usage

```bash
python registerbot.py                        # run the default demo
python registerbot.py --program fibonacci    # Fibonacci sequence
python registerbot.py --program factorial    # 5! = 120
python registerbot.py --program countdown    # countdown loop
python registerbot.py --program max          # find max of two values
python registerbot.py --list                 # list all built-in programs
python registerbot.py --file myprogram.asm   # run your own assembly file
python registerbot.py --no-narrate           # skip LLM, run fast
python registerbot.py --delay 0.5           # auto-step every 0.5 seconds
```

Each step pauses and waits for ENTER, so you can examine register state before continuing.

## Writing your own programs

Assembly files use a simple format:

```asm
; This is a comment
    MOV R0, 10        ; load 10 into R0
    MOV R1, 5         ; load 5 into R1
    ADD R2, R0, R1    ; R2 = R0 + R1
    PRINT R2          ; output the result
LOOP:
    SUB R2, R2, R1    ; decrement
    CMP R2, 0         ; check if zero
    JNZ LOOP          ; loop if not zero
    HALT
```

Registers are `R0` through `R7`. Immediates can be decimal (`42`), negative (`-5`), or hex (`0xFF`). Labels end with `:` and can be on their own line or before an instruction.

## Common fixes

**Narration is slow** — run with `--no-narrate` for instant step-through. Narration queries the LLM once per instruction.

**ollama not running** — the script runs fine without ollama. Narration is simply skipped. To enable it, open a separate terminal and run `ollama serve`.

**Label not found error** — make sure your label is spelled the same way it is defined. Labels are case-insensitive.

**Infinite loop** — the simulator halts automatically after 10,000 steps. This is the loop guard.

## Hardware concept

RegisterBot implements the **fetch-decode-execute cycle** — the fundamental loop every CPU has run since the 1970s. The program counter is the equivalent of a microcontroller's instruction pointer. The ALU (ADD, SUB, MUL, CMP) is the same logic circuit inside every processor. The flags register (Z, N, V) drives conditional branching — the same way status registers work in ARM, x86, RISC-V, and AVR. This is computer architecture implemented from scratch in ~400 lines of Python.

## Credits

- Local LLM narration: [ollama](https://ollama.com) + [Qwen2.5 3B](https://ollama.com/library/qwen2.5)
- Terminal rendering: [rich](https://github.com/Textualize/rich)

Built as part of the **BUILDCORED ORCAS — Day 14: RegisterBot** challenge.
