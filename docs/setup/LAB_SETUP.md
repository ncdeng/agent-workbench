# Lab Machine Setup

This file is the shortest setup path for using this project on another Windows machine with CST 2025.

## Goal

Get the workbench running on the lab computer with the same CST major version, then adjust only local paths and project-local configuration.

## Assumptions

- CST Studio Suite 2025 is already installed on the lab machine
- Python is available from the command line
- You can access this private GitHub repository

## Steps

1. Clone the repository:

```powershell
git clone https://github.com/dengnengcheng0901-commits/cst-agent-workbench.git
cd cst-agent-workbench
```

2. Create a project-local `.env` file:

```powershell
Copy-Item .env.example .env
```

3. Edit `.env`:

```text
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-provider.example/v1
OPENAI_MODEL=gpt-5.4
```

The app will automatically read `.env` and `.env.local` from the project root,
so this configuration only applies to this repository.

4. Open `cst_agent_workbench/config.py` and check:

- `CST_PYTHON_REPO`
- `CST_POSSIBLE_PATHS`
- `CST_DEFAULT_PROJECT` if needed

5. Open `启动.bat` and confirm the `CST_REPO` path matches the lab installation.

6. Start the project:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-ui.txt
python -m pip install --ignore-requires-python --no-index --find-links="D:\Program Files (x86)\CST Studio Suite 2025\Library\Python\repo\simple" cst-studio-suite-link
python app.py
```

Or just run:

```powershell
.\启动.bat
```

7. Open:

```text
http://127.0.0.1:7860
```

## Common Fixes

- If CST cannot be found, update `CST_POSSIBLE_PATHS` and `CST_REPO`.
- If the CST Python package install fails, verify the local CST Python repo path.
- If GitHub clone works but the app cannot run, install dependencies manually before using `启动.bat`.
- If results cannot be read, verify the current project path and that the `.cst` file exists locally.
