def foo(i: int) -> str:
    return f"foo{i}"


print("unrelated change 1")
foo("wrong value")
print("unrelated change 2")
