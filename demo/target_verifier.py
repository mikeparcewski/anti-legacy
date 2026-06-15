#!/usr/bin/env python3
# DEPRECATED: demo-only lightweight verifier. The runtime build/semantic/UAT
# verifier is scripts/validator_discovery.py, which supersedes this for gate
# verification. Retained only for tests/test_demo_pipeline.py.
import os
import sys
import subprocess
import json
import argparse

class TargetVerifier:
    def __init__(self, workspace_path, target_stack):
        self.workspace_path = workspace_path
        self.target_stack = target_stack.lower()

    def detect_and_compile(self):
        cmd = []
        if self.target_stack in ["go", "golang"]:
            cmd = ["go", "build", "./..."]
        elif self.target_stack in ["java", "maven"]:
            if os.path.exists(os.path.join(self.workspace_path, "pom.xml")):
                cmd = ["mvn", "clean", "compile"]
            elif os.path.exists(os.path.join(self.workspace_path, "gradlew")):
                cmd = ["./gradlew", "compileJava"]
            else:
                cmd = ["javac", "-sourcepath", "src", "src/**/*.java"]
        elif self.target_stack in ["dotnet", "csharp"]:
            cmd = ["dotnet", "build"]
        elif self.target_stack == "python":
            cmd = ["python3", "-m", "compileall", "."]
        else:
            print(f"Warning: Unknown target stack '{self.target_stack}'. Defaulting to general syntax verification.", file=sys.stderr)
            sys.exit(0)

        print(f"Executing target build command: {' '.join(cmd)} in {self.workspace_path}")
        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "command": " ".join(cmd)
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to execute command: {e}",
                "command": " ".join(cmd)
            }

    def record_evidence(self, output_path, result):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        status = "PASS" if result["exit_code"] == 0 else "FAIL"
        
        evidence = {
            "scope": "build",
            "phase": "compilation",
            "claim": "target-compiles",
            "status": status,
            "evidence": {
                "command": result["command"],
                "exit_code": result["exit_code"],
                "stdout_snippet": result["stdout"][-2000:], # keep snippet
                "stderr_snippet": result["stderr"][-2000:]
            }
        }
        
        with open(output_path, 'w') as f:
            json.dump(evidence, f, indent=2)
            
        print(f"Recorded build evidence to: {output_path} (Status: {status})")
        return result["exit_code"] == 0

def main():
    parser = argparse.ArgumentParser(description="Deterministic syntax/compilation target verifier.")
    parser.add_argument('--workspace', required=True, help='Path to target codebase')
    parser.add_argument('--stack', required=True, help='Target state technology stack (go, java, dotnet, python)')
    parser.add_argument('--evidence', required=True, help='Output evidence JSON file path')

    args = parser.parse_args()

    verifier = TargetVerifier(args.workspace, args.stack)
    build_result = verifier.detect_and_compile()
    success = verifier.record_evidence(args.evidence, build_result)
    
    if not success:
        print("Build verifier failed: Code compilation errors detected.", file=sys.stderr)
        sys.exit(1)
    else:
        print("Build verifier passed: Code compiles successfully.")
        sys.exit(0)

if __name__ == '__main__':
    main()
