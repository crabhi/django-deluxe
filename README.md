<p align="center">
  <img src="docs/logo-black-on-white.png" alt="django deluxe" width="480">
</p>

Run LLM-generated code in the [Monty](https://github.com/pydantic/monty) sandbox, behind a boundary a human reviews.

A sandboxed function is a class decorated with `@sandboxed`: its docstring is the task, `__call__` the signature of the
generated entry function, and its public methods and attributes everything the generated code can reach. The code
lives in `<app>/generated/<module>.py`, is generated ahead of time and committed; calling an instance runs it in Monty.

- `manage.py sandbox_prompt <module>` prints what the code is generated from.
- `manage.py deluxe_stubs [--check]` writes the stub block the IDE and linters read.
- `django_deluxe.testing.run_generated()` runs generated code in Monty with fakes; `check_generated()` is the contract
  test.

See the docstring of `django_deluxe.sandbox` for the details.
