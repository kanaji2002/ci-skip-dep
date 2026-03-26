You are analyzing a {language} project to find unused packages.

## Project Configuration
- Language: {language}
- Package Manager: {package_manager}
- Package Definition File: {package_file}

## Project Structure
{project_tree}

## Declared Dependencies
### dependencies
{dependencies}

### dev_dependencies
{dev_dependencies}

### {extra_dep_label}
{extra_dependencies}

## package.json scripts
Packages invoked by name in the scripts field are used as CLI tools.
{scripts}

## Source Files (import/require statements)
{source_code}

## Configuration Files
The following config files may reference packages as strings (not import statements).
Count these as usage even if the package never appears in an import.
{config_references}

## Language-Specific Rules
{language_rules}

## Common Rules
- A package is used if it appears in an import/require statement OR is referenced by name in a config file
- Subpath imports count as usage of the root package
  Example: {subpath_example}
- Side-effect imports count as usage: `import 'pkg'` means pkg IS used
- Ignore built-in modules: {builtin_modules}
- {missing_dep_rule}
- Packages with only dynamic imports → report in dynamic_usage_detected,
  do NOT include in unused_dependencies
- Use file paths to determine context:
  test/, spec/, __tests__/ → test code
  *.config.js, *.config.ts → config code

## Steps
1. Extract declared package names from each dependency category
2. Find package usage across ALL evidence sources:
   a. Source file imports/require (including side-effect imports like `import 'pkg'`)
   b. Config file string references (e.g. `"transform": ["babel-plugin-foo"]`)
   c. tsconfig.json types array for @types/* packages
3. Compare declared vs used per category
4. Identify unused and missing packages

## Output
Return ONLY valid JSON:
{
  "unused_dependencies": [],
  "unused_dev_dependencies": [],
  "unused_extra_dependencies": [],
  "missing_dependencies": [],
  "dynamic_usage_detected": [],
  "notes": []
}
Do not include explanations or markdown.
