#!/usr/bin/env python3
import argparse
import fnmatch
import os
import shlex
import subprocess
import sys
from collections import deque
from typing import Deque, List, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Vibe Analyzer: run multiple agents over input files with a prompt template."
        )
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="inputs",
        help="Directory with input files to iterate over (default: inputs).",
    )
    parser.add_argument(
        "--prompt",
        "--prompts",
        default="TASK.md",
        help="Prompt template file (default: TASK.md).",
    )
    parser.add_argument(
        "--placeholder",
        default="{{INPUT_FILE}}",
        help="Placeholder token in the prompt template (default: {{INPUT_FILE}}).",
    )
    parser.add_argument(
        "--pattern",
        default="*",
        help="Glob pattern for input files (default: *).",
    )
    parser.add_argument(
        "--output-dir",
        default="runs",
        help="Directory for prompts and logs (default: runs).",
    )
    parser.add_argument(
        "--sample-run",
        action="store_true",
        help="Run only the first matching input file.",
    )
    return parser.parse_args()


def load_template(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def list_input_files(input_dir: str, pattern: str) -> list[str]:
    entries = []
    with os.scandir(input_dir) as it:
        for entry in it:
            if not entry.is_file():
                continue
            if not fnmatch.fnmatch(entry.name, pattern):
                continue
            entries.append(entry.path)
    return sorted(entries, key=lambda p: os.path.basename(p))


def render_prompt(
    template: str,
    placeholder: str,
    input_file: str,
    input_rel_path: str,
) -> str:
    rendered = template.replace(placeholder, input_rel_path)
    rendered = rendered.replace("{input_file}", input_file)
    rendered = rendered.replace("{input_path}", input_rel_path)
    return rendered


def run_claude(prompt_text: str) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    cmd = ["claude", "-p", prompt_text, "--allowedTools", "Read,Grep,Glob,Edit,Update"]
    return cmd, subprocess.run(cmd, capture_output=True, text=True)


def run_codex(prompt_path: str, agent: str) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    cmd = [
        "codex",
        "run",
        "--agent",
        agent,
        "--read",
        "--network",
        "--prompt-file",
        prompt_path,
    ]
    return cmd, subprocess.run(cmd, capture_output=True, text=True)


def run_gemini(prompt_text: str) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    cmd = ["gemini", "-p", prompt_text, "-y", "--output-format", "text"]
    return cmd, subprocess.run(cmd, capture_output=True, text=True)


def is_rate_limited(output: str) -> bool:
    lowered = output.lower()
    tokens = ["rate limit", "rate-limit", "too many requests", "429"]
    return any(token in lowered for token in tokens)


def main() -> int:
    args = parse_args()

    template = load_template(args.prompt)
    if args.placeholder not in template:
        print(
            f"Error: placeholder token {args.placeholder!r} not found in {args.prompt}.",
            file=sys.stderr,
        )
        return 2

    input_files = list_input_files(args.input_dir, args.pattern)
    if not input_files:
        print(f"No input files found in {args.input_dir!r}.", file=sys.stderr)
        return 1

    output_dir = args.output_dir
    prompts_dir = os.path.join(output_dir, "prompts")
    logs_dir = os.path.join(output_dir, "logs")
    ensure_dir(prompts_dir)
    ensure_dir(logs_dir)

    agents: Deque[str] = deque(["claude", "codex", "gemini"])

    base_dir = os.path.dirname(os.path.abspath(__file__))
    for input_path in input_files:
        input_file = os.path.basename(input_path)
        input_rel_path = os.path.relpath(input_path, start=base_dir)
        prompt_text = render_prompt(
            template=template,
            placeholder=args.placeholder,
            input_file=input_file,
            input_rel_path=input_rel_path,
        )

        prompt_path = os.path.join(prompts_dir, f"{input_file}.prompt.md")
        with open(prompt_path, "w", encoding="utf-8") as handle:
            handle.write(prompt_text)
        output_path = os.path.join(
            output_dir, f"{os.path.splitext(input_file)[0]}.json"
        )

        attempts: List[Tuple[str, str]] = []
        for _ in range(len(agents)):
            agent = agents[0]
            agent_log_dir = os.path.join(logs_dir, agent)
            ensure_dir(agent_log_dir)
            log_path = os.path.join(agent_log_dir, f"{input_file}.log")

            print(f"  ▶ Trying {agent} for {input_rel_path}...")
            if agent == "claude":
                cmd_list, result = run_claude(prompt_text)
            elif agent == "gemini":
                cmd_list, result = run_gemini(prompt_text)
            else:
                cmd_list, result = run_codex(prompt_path, agent)
            combined_output = "\n".join(
                part for part in [result.stdout.strip(), result.stderr.strip()] if part
            )
            with open(log_path, "w", encoding="utf-8") as log_handle:
                log_handle.write(
                    f"# Command: {' '.join(shlex.quote(part) for part in cmd_list)}\n"
                )
                log_handle.write(f"# Exit code: {result.returncode}\n\n")
                if result.stdout:
                    log_handle.write("## STDOUT\n")
                    log_handle.write(result.stdout)
                    if not result.stdout.endswith("\n"):
                        log_handle.write("\n")
                if result.stderr:
                    log_handle.write("\n## STDERR\n")
                    log_handle.write(result.stderr)
                    if not result.stderr.endswith("\n"):
                        log_handle.write("\n")

            if result.returncode == 0:
                with open(output_path, "w", encoding="utf-8") as out_handle:
                    out_handle.write(result.stdout)
                break

            attempts.append((agent, log_path))
            if is_rate_limited(combined_output):
                failed_agent = agents.popleft()
                agents.append(failed_agent)
                continue

            print(
                f"Agent {agent!r} failed for {input_file!r} "
                f"(exit {result.returncode}). See {log_path}.",
                file=sys.stderr,
            )
            return result.returncode
        else:
            last_attempt = attempts[-1] if attempts else ("unknown", "unknown")
            print(
                f"All agents rate limited for {input_file!r}. "
                f"Last attempt: {last_attempt[0]!r} ({last_attempt[1]}).",
                file=sys.stderr,
            )
            return 3

        if args.sample_run:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
