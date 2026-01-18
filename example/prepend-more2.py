def foo(i: int) -> str:
    print("unrelated change")
    print("unrelated change")
    return f"foo{i}"


foo("wrong value")
