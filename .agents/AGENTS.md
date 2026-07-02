# Project Rules & Customizations

## 1. Avoid Shadowing FastAPI status Module
* **Rule**: Never declare a local variable, exception alias, loop iterator, or parameter named `status` in any Python file that imports `from fastapi import status`.
* **Descriptive Alternatives**: Use specific prefixes/suffixes such as:
  * `project_status`
  * `analysis_status`
  * `embedding_status`
  * `candidate_status`
  * `job_status`
  * `http_status`
* **Reasoning**: Shadowing the module name with a local variable causes `UnboundLocalError` when accessing standard status code references (e.g. `status.HTTP_409_CONFLICT`) prior to the local assignment line.
