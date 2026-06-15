#!/usr/bin/env python3
"""
Semantic Join Analyzer for multi-repository legacy modernizations.
Scans source files to identify endpoints and API calls, mapping dependencies across services.
"""
import os
import sys
import re
import json

# Global map of discovered Java class names to their full package names
JAVA_CLASSES = {}

# Regexes for route/endpoint detection
ROUTE_REGEXES = {
    "python": [
        r'@(?:\w+\.)?(?:route|get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']',
        r'route\s*=\s*["\']([^"\']+)["\']'
    ],
    "java": [
        r'@(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']',
        r'@(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\s*\(\s*path\s*=\s*["\']([^"\']+)["\']'
    ],
    "kotlin": [
        r'@(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']',
        r'@(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)\s*\(\s*path\s*=\s*["\']([^"\']+)["\']'
    ],
    "go": [
        r'\.(?:HandleFunc|GET|POST|PUT|DELETE|Handle)\s*\(\s*["\']([^"\']+)["\']',
        r'http\.Handle\s*\(\s*["\']([^"\']+)["\']'
    ],
    "rust": [
        r'#\[(?:get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']'
    ],
    "csharp": [
        r'\[(?:HttpGet|HttpPost|HttpPut|HttpDelete|Route)\s*\(\s*["\']([^"\']+)["\']'
    ],
    "ruby": [
        r'(?:get|post|put|delete)\s+["\']([^"\']+)["\']'
    ],
    "swift": [
        r'@(?:get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']'
    ],
    "javascript": [
        r'\.(?:get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']'
    ],
    "typescript": [
        r'\.(?:get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']'
    ],
    "php": [
        r'(?:Route::(?:get|post|put|delete)|->(?:get|post|put|delete))\s*\(\s*["\']([^"\']+)["\']'
    ],
    "scala": [
        r'@(?:GET|POST|PUT|DELETE|Path)\s*\(\s*["\']([^"\']+)["\']'
    ]
}

# Regexes for client calls containing paths or urls
CLIENT_REGEXES = [
    # Match standard http/https strings
    r'https?://[a-zA-Z0-9_.-]+(?::\d+)?(/[a-zA-Z0-9_/.-]*)',
    # Match requests calls
    r'requests\.(?:get|post|put|delete|request)\s*\(\s*["\']([^"\']+)["\']',
    # Match standard path-like strings in configs/constants
    r'["\'](/api/[a-zA-Z0-9_/.-]+)["\']',
    # Match JS/TS fetch/axios
    r'(?:fetch|axios\.(?:get|post|put|delete))\s*\(\s*["\']([^"\']+)["\']',
    # Match C# HttpClient
    r'HttpClient\.(?:Get|Post|Put|Delete)Async\s*\(\s*["\']([^"\']+)["\']',
    # Match Rust reqwest
    r'reqwest::(?:get|Client::new\(\)\.(?:get|post))\s*\(\s*["\']([^"\']+)["\']'
]

def normalize_field(field):
    # Split camelCase by converting e.g., 'cardNumber' to 'card_Number'
    field_split = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', field)
    # Normalize by converting to lowercase, replacing hyphens/underscores with space, and splitting into words
    words = re.findall(r'[a-zA-Z0-9]+', field_split.lower())
    # Expand common abbreviations and typos
    abbr = {
        "num": "number",
        "no": "number",
        "acc": "account",
        "acct": "account",
        "exp": "expiration",
        "expiraion": "expiration",
        "cd": "code",
        "cvv": "cvv",
        "id": "identifier",
        "crd": "card"
    }
    normalized = []
    for w in words:
        normalized.append(abbr.get(w, w))
    return set(normalized)

def match_fields_semantic(fields1, fields2):
    """Compares two lists of fields. Returns matching pairs if there is significant semantic overlap."""
    if not fields1 or not fields2:
        return []
        
    matches = []
    for f1 in fields1:
        words1 = normalize_field(f1)
        if not words1:
            continue
        for f2 in fields2:
            words2 = normalize_field(f2)
            if not words2:
                continue
            # If they share significant words (excluding generic words like 'card') or are an exact match
            common = words1 & words2
            significant_common = common - {"card", "filler"}
            if significant_common or (common and (common == words1 or common == words2)):
                matches.append((f1, f2))
                break
                
    return matches

def normalize_path(path):
    """Normalize path by replacing dynamic parameters (like {id}, <id>, :id) with *."""
    if not path:
        return ""
    # Strip query parameters
    path = path.split('?')[0]
    # Replace {param}, <param>, :param with *
    path = re.sub(r'\{[^}]+\}', '*', path)
    path = re.sub(r'<[^>]+>', '*', path)
    path = re.sub(r':[a-zA-Z0-9_-]+', '*', path)
    # Ensure starting slash
    if not path.startswith('/'):
        path = '/' + path
    # Remove trailing slash unless it's just /
    if path.endswith('/') and len(path) > 1:
        path = path[:-1]
    return path

def scan_file(file_path, language):
    """Scan a single file for endpoints and client calls."""
    from urllib.parse import urlparse
    endpoints = []
    client_calls = []
    
    try:
        ext = os.path.splitext(file_path)[1].lower()
        
        # Special Java scanning for classes/interfaces and their dependencies
        if language == "java" and ext == ".java":
            # Ignore test files and targets
            if "src/test" in file_path or "target" in file_path:
                return [], []
            with open(file_path, 'r', errors='ignore') as f:
                content = f.read()
            pkg_match = re.search(r"package\s+([\w\.]+);", content)
            package_name = pkg_match.group(1) if pkg_match else ""
            class_match = re.search(r"(?:public\s+|protected\s+|private\s+)?(class|interface|enum)\s+(\w+)", content)
            class_name = class_match.group(2) if class_match else os.path.splitext(os.path.basename(file_path))[0]
            full_class_path = f"/class/{package_name}.{class_name}" if package_name else f"/class/{class_name}"
            
            # Extract fields for schema matching
            fields = []
            for line in content.splitlines():
                field_match = re.search(r'(?:private|protected|public)\s+[\w<>]+\s+(\w+)\s*(?:=[\s\S]*?)?;', line)
                if field_match:
                    fields.append(field_match.group(1))

            # Every class defines a structural API endpoint
            endpoints.append({
                "path": full_class_path,
                "file": os.path.basename(file_path),
                "rel_path": file_path,
                "line": 1,
                "fields": fields
            })
            
            # Scan for usages of other classes as client calls
            known_classes = JAVA_CLASSES
            for short_name, full_name in known_classes.items():
                if short_name == class_name:
                    continue
                if re.search(r'\b' + re.escape(short_name) + r'\b', content):
                    client_calls.append({
                        "url_or_path": f"/class/{full_name}",
                        "path": f"/class/{full_name}",
                        "file": os.path.basename(file_path),
                        "rel_path": file_path,
                        "line": 1
                    })
        
        # Special COBOL/BMS scanning
        if language == "cobol" or ext in [".cbl", ".cob", ".cpy", ".bms"]:
            with open(file_path, 'r', errors='ignore') as f:
                content = f.read()
                
            fields = []
            if ext != ".bms":
                for line in content.splitlines():
                    cob_field_match = re.search(r'^\s*\d+\s+([\w-]+)\s+PIC', line, re.IGNORECASE)
                    if cob_field_match:
                        fields.append(cob_field_match.group(1))
                        
            if ext == ".bms":
                # ...
                # BMS map screen definitions are endpoints
                map_match = re.search(r'^([\w-]+)\s+(?:DFHMSD|DFHMDI)', content, re.IGNORECASE | re.MULTILINE)
                if map_match:
                    map_name = map_match.group(1).upper()
                    endpoints.append({
                        "path": f"/bms/{map_name}",
                        "file": os.path.basename(file_path),
                        "rel_path": file_path,
                        "line": 1
                    })
            else:
                # COBOL programs
                prog_match = re.search(r'PROGRAM-ID\.\s+([\w-]+)\.', content, re.IGNORECASE)
                prog_name = prog_match.group(1).upper() if prog_match else os.path.splitext(os.path.basename(file_path))[0].upper()
                
                # Every program is a callable endpoint internally
                endpoints.append({
                    "path": f"/program/{prog_name}",
                    "file": os.path.basename(file_path),
                    "rel_path": file_path,
                    "line": 1,
                    "fields": fields
                })
                
                # If CICS program, it's also an online controller endpoint
                if "EXEC CICS" in content or "dfhaid" in content.lower():
                    endpoints.append({
                        "path": f"/cics/{prog_name}",
                        "file": os.path.basename(file_path),
                        "rel_path": file_path,
                        "line": 1
                    })
                    
                # Extract calls
                calls = re.findall(r'CALL\s+[\'"]([\w-]+)[\'"]', content, re.IGNORECASE)
                for c in calls:
                    c_name = c.upper()
                    if c_name not in ["SYSTEM", "DFHPC"]: # Skip generic frameworks
                        client_calls.append({
                            "url_or_path": f"/program/{c_name}",
                            "path": f"/program/{c_name}",
                            "file": os.path.basename(file_path),
                            "rel_path": file_path,
                            "line": 1
                        })
                        
                # Extract CICS links
                cics_links = re.findall(r'EXEC\s+CICS\s+(?:LINK|XCTL)\s+PROGRAM\s*\(\s*[\'"]([\w-]+)[\'"]\s*\)', content, re.IGNORECASE | re.DOTALL)
                for l in cics_links:
                    l_name = l.upper()
                    client_calls.append({
                        "url_or_path": f"/cics/{l_name}",
                        "path": f"/cics/{l_name}",
                        "file": os.path.basename(file_path),
                        "rel_path": file_path,
                        "line": 1
                    })
                    
                # Extract CICS maps referenced
                maps = re.findall(r'MAP\s*\(\s*[\'"]([\w-]+)[\'"]\s*\)', content, re.IGNORECASE)
                for m in maps:
                    m_name = m.upper()
                    client_calls.append({
                        "url_or_path": f"/bms/{m_name}",
                        "path": f"/bms/{m_name}",
                        "file": os.path.basename(file_path),
                        "rel_path": file_path,
                        "line": 1
                    })
            return endpoints, client_calls
            
        # Standard languages
        with open(file_path, 'r', errors='ignore') as f:
            for idx, line in enumerate(f, 1):
                # Check endpoints
                is_endpoint_line = False
                if language in ROUTE_REGEXES:
                    for regex in ROUTE_REGEXES[language]:
                        for match in re.finditer(regex, line):
                            path = match.group(1)
                            is_endpoint_line = True
                            if not any(e["path"] == path for e in endpoints):
                                endpoints.append({
                                    "path": path,
                                    "file": os.path.basename(file_path),
                                    "rel_path": file_path,
                                    "line": idx
                                })
                
                # Check client calls (only if not a route definition line)
                if not is_endpoint_line:
                    for regex in CLIENT_REGEXES:
                        for match in re.finditer(regex, line):
                            raw_match = match.group(1) if len(match.groups()) > 0 else match.group(0)
                            raw_match = raw_match.strip('"\')')
                            
                            parsed = urlparse(raw_match)
                            path = parsed.path if (parsed.scheme or parsed.netloc) else raw_match
                            if path and len(path) > 1:  # Avoid matching single slashes
                                # Filter out false positive static URLs (like XML schema URLs)
                                if "xml" in raw_match or "schema" in raw_match or "xslt" in raw_match or "apache.org" in raw_match or "w3.org" in raw_match:
                                    continue
                                if not any(c["path"] == path for c in client_calls):
                                    client_calls.append({
                                        "url_or_path": raw_match,
                                        "path": path,
                                        "file": os.path.basename(file_path),
                                        "rel_path": file_path,
                                        "line": idx
                                    })
    except Exception as e:
        print(f"Warning: Failed to scan {file_path}: {e}")
        
    return endpoints, client_calls

def analyze_project(config_path=".anti-legacy/config.json"):
    """Reads the anti-legacy config, scans source apps, and maps the relationships."""
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}. Run anti-legacy:setup first.")
        sys.exit(1)
        
    with open(config_path) as f:
        config = json.load(f)
        
    source_apps = config.get("source_apps", [])
    if not source_apps:
        print("No source apps registered in config.json.")
        return
        
    # Pass 0: Populate JAVA_CLASSES for all Java apps to resolve internal method/class calls
    global JAVA_CLASSES
    JAVA_CLASSES.clear()
    for app in source_apps:
        app_path = app["path"]
        app_lang = app.get("language", "python").lower()
        if app_lang == "java":
            resolved_path = app_path if os.path.isabs(app_path) else os.path.abspath(app_path)
            if os.path.isdir(resolved_path):
                for root, _, files in os.walk(resolved_path):
                    for file in files:
                        if file.endswith(".java"):
                            file_path = os.path.join(root, file)
                            if "src/test" in file_path or "target" in file_path:
                                continue
                            try:
                                with open(file_path, 'r', errors='ignore') as f:
                                    content = f.read()
                                pkg_match = re.search(r"package\s+([\w\.]+);", content)
                                package_name = pkg_match.group(1) if pkg_match else ""
                                class_match = re.search(r"(?:public\s+|protected\s+|private\s+)?(class|interface|enum)\s+(\w+)", content)
                                if class_match:
                                    class_name = class_match.group(2)
                                    full_name = f"{package_name}.{class_name}" if package_name else class_name
                                    JAVA_CLASSES[class_name] = full_name
                            except Exception:
                                pass

    services = {}
    
    # 1. Scan each source app
    for app in source_apps:
        app_name = app["name"]
        app_path = app["path"]
        app_lang = app.get("language", "python").lower()
        
        # Resolve path relative to current workspace if it's relative
        resolved_path = app_path
        if not os.path.isabs(resolved_path):
            resolved_path = os.path.abspath(resolved_path)
            
        print(f"Scanning app '{app_name}' at {resolved_path} ({app_lang})...")
        
        endpoints = []
        client_calls = []
        
        if os.path.isdir(resolved_path):
            for root, _, files in os.walk(resolved_path):
                for file in files:
                    # Filter files based on language/extensions
                    ext = os.path.splitext(file)[1].lower()
                    supported_extensions = {
                        "python": [".py"],
                        "java": [".java"],
                        "kotlin": [".kt", ".kts"],
                        "go": [".go"],
                        "rust": [".rs"],
                        "csharp": [".cs"],
                        "ruby": [".rb"],
                        "swift": [".swift"],
                        "javascript": [".js", ".jsx"],
                        "typescript": [".ts", ".tsx"],
                        "cpp": [".cpp", ".cc", ".cxx", ".h", ".hpp"],
                        "scala": [".scala"],
                        "php": [".php"],
                        "cobol": [".cbl", ".cob", ".cpy", ".bms"]
                    }
                    
                    should_scan = False
                    if app_lang in supported_extensions:
                        if ext in supported_extensions[app_lang]:
                            should_scan = True
                    if not should_scan:
                        all_exts = [e for exts in supported_extensions.values() for e in exts]
                        if ext in all_exts:
                            should_scan = True
                        
                    if should_scan:
                        file_path = os.path.join(root, file)
                        eps, cls = scan_file(file_path, app_lang)
                        endpoints.extend(eps)
                        client_calls.extend(cls)

        services[app_name] = {
            "path": app_path,
            "language": app_lang,
            "endpoints": endpoints,
            "client_calls": client_calls
        }
        
    # 2. Map client calls to endpoints across services
    relations = []
    dangling_calls = []
    
    # Flat list of all endpoints for matching
    all_endpoints = []
    for service_name, data in services.items():
        for ep in data["endpoints"]:
            all_endpoints.append({
                "service": service_name,
                "path": ep["path"],
                "normalized": normalize_path(ep["path"]),
                "file": ep["file"],
                "line": ep["line"]
            })
            
    for service_name, data in services.items():
        for call in data["client_calls"]:
            norm_call_path = normalize_path(call["path"])
            matched = False
            
            for ep in all_endpoints:
                if ep["service"] == service_name:
                    continue  # Skip self-calls for inter-repo mapping
                if ep["normalized"] == norm_call_path:
                    # Match found!
                    relations.append({
                        "source": service_name,
                        "target": ep["service"],
                        "type": "http",
                        "path": call["path"],
                        "file": call["file"],
                        "line": call["line"],
                        "matched_endpoint": ep["path"]
                    })
                    matched = True
                    break
                    
            if not matched:
                dangling_calls.append({
                    "source": service_name,
                    "url_or_path": call["url_or_path"],
                    "path": call["path"],
                    "file": call["file"],
                    "line": call["line"]
                })
                
    # 2b. Map schema semantic matches (payload/interface matching) across services
    for s1_name, s1_data in services.items():
        for ep1 in s1_data["endpoints"]:
            fields1 = ep1.get("fields", [])
            if not fields1:
                continue
            for s2_name, s2_data in services.items():
                if s1_name == s2_name:
                    continue  # Only cross-repository matching
                # Enforce alphabetical order to prevent duplicate relations
                if s1_name >= s2_name:
                    continue
                for ep2 in s2_data["endpoints"]:
                    fields2 = ep2.get("fields", [])
                    if not fields2:
                        continue
                    
                    matched_pairs = match_fields_semantic(fields1, fields2)
                    if len(matched_pairs) >= 3:
                        match_desc = ", ".join([f"{f1} <-> {f2}" for f1, f2 in matched_pairs])
                        relations.append({
                            "source": s1_name,
                            "target": s2_name,
                            "type": "schema_match",
                            "path": ep1["path"],
                            "file": ep1["file"],
                            "line": ep1["line"],
                            "matched_endpoint": ep2["path"],
                            "details": f"Semantic payload match on fields: {match_desc}"
                        })

    # 3. Save JSON graph
    output_dir = config.get("paths", {}).get("requirements_dir") or ".anti-legacy/requirements"
    os.makedirs(output_dir, exist_ok=True)
    graph_path = os.path.join(output_dir, "semantic_join_graph.json")
    
    graph_data = {
        "services": services,
        "relations": relations,
        "dangling_calls": dangling_calls
    }
    
    with open(graph_path, 'w') as f:
        json.dump(graph_data, f, indent=2)
    print(f"Wrote Semantic Join Graph to {graph_path}")
    
    # 4. Generate Markdown report
    report_path = config.get("paths", {}).get("semantic_join_report", ".anti-legacy/requirements/semantic_join_report.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w') as f:
        f.write("# Semantic Join Validation Report\n\n")
        f.write("This report validates interface boundaries and communication channels across your microservices/repositories.\n\n")
        
        f.write("## 1. Services Overview\n\n")
        f.write("| Service | Language | Endpoints Found | Client Calls Found |\n")
        f.write("| --- | --- | --- | --- |\n")
        for name, data in services.items():
            f.write(f"| {name} | {data['language']} | {len(data['endpoints'])} | {len(data['client_calls'])} |\n")
        f.write("\n")
        
        f.write("## 2. Inter-Repository Connections\n\n")
        if relations:
            f.write("| Source Service | Target Service | Connection Path / Type | Defined Endpoint | Location | Details |\n")
            f.write("| --- | --- | --- | --- | --- | --- |\n")
            for rel in relations:
                details = rel.get("details", "Direct HTTP/API call")
                f.write(f"| {rel['source']} | {rel['target']} | `{rel['path']}` | `{rel['matched_endpoint']}` | `{rel['file']}:{rel['line']}` | {details} |\n")
        else:
            f.write("*No cross-repository connections detected.*\n")
        f.write("\n")
        
        f.write("## 3. Dangling Call Warnings (Possible Integration Gaps)\n\n")
        if dangling_calls:
            f.write("> [!WARNING]\n")
            f.write("> The following HTTP calls were found in your codebase but do not match any defined endpoints in other imported repositories.\n\n")
            f.write("| Calling Service | Destination Path / URL | File Location |\n")
            f.write("| --- | --- | --- |\n")
            for dc in dangling_calls:
                f.write(f"| {dc['source']} | `{dc['url_or_path']}` | `{dc['file']}:{dc['line']}` |\n")
        else:
            f.write("> [!NOTE]\n")
            f.write("> All detected client calls successfully map to defined endpoints across repositories. ✓\n")
            
    print(f"Wrote Semantic Join Report to {report_path}")

if __name__ == "__main__":
    analyze_project()
