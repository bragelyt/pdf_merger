# PDF merger

Merges all PDFs in `input/` into one print-ready A4 PDF in `output/`.
Pages that aren't A4 are scaled to fit A4 without any loss of quality.
Links, form fields and bookmarks are kept.

## Setup (once)

Needs Python 3.10 or newer.

From the project folder:

```sh
python3 -m venv .venv               # create a local Python environment
source .venv/bin/activate           # activate it
pip install -r requirements.txt     # install pypdf into it
```

On Windows, activate with `.venv\Scripts\activate` instead.

## Run

1. Put your PDFs in `input/`. They are merged in alphabetical order,
   so name them `01_...`, `02_...` to control the order.
2. From the project folder, activate the environment (once per terminal
   window; the prompt shows `(.venv)` when it's active) and run the script:

   ```sh
   source .venv/bin/activate
   python merge_a4.py
   ```

3. The result is in `output/`, named after the current time,
   e.g. `output/2026-01-31_14-05-42.pdf`.

Add `--no-upscale` to keep pages that are smaller than A4 at their original
size instead of enlarging them:

```sh
python merge_a4.py --no-upscale
```

Run `deactivate` when you're done, or just close the terminal window.
