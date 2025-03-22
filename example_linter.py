import sys

original_output = [
    '---original output 1',
    '+++original output 2',
    '   original output 3',
]


def print_original_output():
    for line in original_output:
        print(line)


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'setUp':
        print_original_output()
    elif command == 'new_errors':
        print_original_output()
        print('+++new output 1')
    elif command == 'fewer_errors':
        print('---original output 1')
        print('   original output 3')
    elif command == 'removed_and_added':
        print('+++new output 1')
        print('---original output 1')
        print('+++original output 2')
    elif command == 'no_changes':
        print_original_output()
