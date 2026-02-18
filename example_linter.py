import sys

original_output = [
    '---original output 1',
    '+++original output 2',
    '   original output 3',
]

new_errors_output = original_output + [
    '+++new output 1',
]


fewer_errors_output = [
    '---original output 1',
    '   original output 3',
]

removed_and_added_output = [
    '+++new output 1',
    '---original output 1',
    '+++original output 2',
]


def _print_original_output():
    for line in original_output:
        print(line)


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'setUp':
        _print_original_output()
    elif command == 'new_errors':
        for line in new_errors_output:
            print(line)
    elif command == 'fewer_errors':
        for line in fewer_errors_output:
            print(line)
    elif command == 'removed_and_added':
        for line in removed_and_added_output:
            print(line)
    elif command == 'no_changes':
        _print_original_output()
