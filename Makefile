all: report-new-linter-errors

report-new-linter-errors: report_new_linter_errors.py
	install -m 755 report_new_linter_errors.py report-new-linter-errors
