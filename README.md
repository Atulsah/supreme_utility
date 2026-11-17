### Supreme Utility

For Supreme Utility Documents

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app supreme_utility
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/supreme_utility
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade
### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit

## Fresh install notes

- `bench get-app` / `bench --site <site> install-app supreme_utility` (then `bench --site <site> migrate`).
- `install.py` (after_install / after_migrate) creates the **PJP Plan** custom field on Expense Claim and Employee Advance and the PJP cost heads (Expense Claim Types). There are no patches.
- **Sales Calendar** page (`/app/sales-calendar`): every Sales Person x every day. Red = Leave (HRMS Leave Application), Green = Tour, Blue = Event / working, Yellow = Idle, Grey = Holiday. Hover for details, click to open.
- Lead / Opportunity are created from PJP visits with *Visit With = Lead* on Submit and on Update; a report popup lists what was created, skipped or failed (failures are also in Error Log).

## Sales Calendar: hour view
- A working day (not leave / tour / holiday) is split into hourly slots (default 10:00 to 19:00; change it in **Sales Calendar Settings**: Work Start Hour / Work End Hour, 3 to 12 hours).
- Slot colour: orange = Sales call / Follow-up, green = Customer visit, purple = Documentation, teal = Report / Analysis, blue = Other work, hatched = suggested from system activity (Email / Call Communication, Quotation, Sales Order created in that hour), yellow = idle hour, grey = outside Employee Checkin IN/OUT.
- Data: Timesheet rows (Activity Types are created by install.py), calendar Events, system activity, Employee Checkin. Same work over 2-3 hours = one bar in Week view.
- Click an idle hour to log what was done: it creates / extends that day's draft Timesheet. A Sales Person can log only for themselves (Sales Manager / HR / System Manager can log for anyone).
- A past working day with nothing logged is yellow. Future days stay plain.
- Minute accuracy: a Timesheet row 10:45 to 11:50 is drawn exactly so. Day / Week view place bars by real minutes. Month view fills each hour box left to right by minutes and shows a thin bar at the bottom = share of the working hours logged. An idle gap shorter than 10 minutes between two pieces of work is ignored.
