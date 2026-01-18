def foo(i: int) -> str:
    return f"foo{i}"

print("unrelated change")
print("unrelated change")

foo("wrong value")
