# Third-party licences: subject tracking and rep counting

Repcut does not vendor any Ultralytics code. It calls the `ultralytics` Python
package (installed out of band by `make setup-gpu`, never a declared
dependency, never in CI) and two weight files that `scripts/fetch_models.py`
downloads into `$DATA_DIR/models/yolo/` (never committed).

| Part | Source | Licence |
|---|---|---|
| `ultralytics` package | Ultralytics, PyPI | AGPL-3.0 |
| YOLO26n detection weights (`yolo26n.pt`) | Ultralytics assets release `v8.4.0` on GitHub, sha256 pinned in `gym/weights.py` | AGPL-3.0 |
| YOLO26n-pose weights (`yolo26n-pose.pt`) | same release, sha256 pinned | AGPL-3.0 |

AGPL-3.0 is Repcut's own licence, so the two are compatible. Ultralytics also
sells an enterprise licence for closed-source use; Repcut needs none, because
it is published under AGPL-3.0 in full.

Two consequences to keep in mind:

- A hosted Repcut (Prompt 13 territory) must offer its source to its users,
  as AGPL-3.0 requires of Repcut anyway.
- Ultralytics opens a `.pt` file with `torch.load`, which unpickles. Repcut
  hands it a weights file only after its size and sha256 match the pin at
  that moment (`gym/weights.verify_weights`); a changed file is refused.
