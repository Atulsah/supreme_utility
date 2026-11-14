"""HRMS tools (requires the hrms app). All read-only and user-permission checked (get_list)."""
import frappe
from frappe.utils import nowdate

ME_WORDS = {"me", "my", "self", "mera", "meri", "mere", "main", "i"}

HR_TOOLS = [
	{
		"name": "get_employee_info",
		"description": "Employee details: gender, date of birth, designation, department, branch, status, joining date, reports to, phone/email. Use employee='me' for the logged-in user.",
		"parameters": {
			"type": "object",
			"properties": {"employee": {"type": "string", "description": "Name or ID (partial match works), or 'me'"}},
			"required": ["employee"],
		},
	},
	{
		"name": "get_birthdays",
		"description": "Birthdays of active employees in a month (default: current month), sorted by day. Also flags today's birthdays.",
		"parameters": {
			"type": "object",
			"properties": {"month": {"type": "integer", "description": "1-12, default current month"}},
		},
	},
	{
		"name": "get_employee_count",
		"description": "Total count of active employees, by department.",
		"parameters": {"type": "object", "properties": {}},
	},
	{
		"name": "get_attendance_summary",
		"description": "Attendance of one employee over a date range: days Present/Absent/On Leave/Half Day.",
		"parameters": {
			"type": "object",
			"properties": {
				"employee": {"type": "string", "description": "Naam/ID ya 'me'"},
				"from_date": {"type": "string", "description": "YYYY-MM-DD"},
				"to_date": {"type": "string", "description": "YYYY-MM-DD"},
			},
			"required": ["employee", "from_date", "to_date"],
		},
	},
	{
		"name": "get_attendance_on_date",
		"description": "Attendance of everyone on a single day: how many present/absent/on leave, and names of absent employees.",
		"parameters": {
			"type": "object",
			"properties": {"date": {"type": "string", "description": "YYYY-MM-DD, default today"}},
		},
	},
	{
		"name": "get_leave_balance",
		"description": "Leave balance of an employee by leave type (total, taken, remaining).",
		"parameters": {
			"type": "object",
			"properties": {
				"employee": {"type": "string", "description": "Naam/ID ya 'me'"},
				"date": {"type": "string", "description": "YYYY-MM-DD, default today"},
			},
			"required": ["employee"],
		},
	},
	{
		"name": "get_leave_applications",
		"description": "List of leave applications. Employee is optional (omit for everyone). Status: Open (pending), Approved, Rejected.",
		"parameters": {
			"type": "object",
			"properties": {
				"employee": {"type": "string", "description": "Optional naam/ID ya 'me'"},
				"status": {"type": "string", "description": "Open / Approved / Rejected"},
				"count": {"type": "integer", "description": "Default 10, max 30"},
			},
		},
	},
	{
		"name": "get_last_salary_slips",
		"description": "Latest salary slips of an employee: gross, deductions, net pay.",
		"parameters": {
			"type": "object",
			"properties": {
				"employee": {"type": "string", "description": "Naam/ID ya 'me'"},
				"count": {"type": "integer", "description": "Default 1, max 6"},
			},
			"required": ["employee"],
		},
	},
	{
		"name": "get_expense_claims",
		"description": "List of expense claims. Employee is optional. approval_status: Draft / Approved / Rejected.",
		"parameters": {
			"type": "object",
			"properties": {
				"employee": {"type": "string", "description": "Optional naam/ID ya 'me'"},
				"approval_status": {"type": "string"},
				"count": {"type": "integer", "description": "Default 10, max 30"},
			},
		},
	},
]

HR_PERM = {
	"get_employee_info": "Employee",
	"get_employee_count": "Employee",
	"get_birthdays": "Employee",
	"get_attendance_summary": "Attendance",
	"get_attendance_on_date": "Attendance",
	"get_leave_balance": "Leave Allocation",
	"get_leave_applications": "Leave Application",
	"get_last_salary_slips": "Salary Slip",
	"get_expense_claims": "Expense Claim",
}


def _resolve_employee(text):
	"""Returns (row, error). 'me' means the Employee linked to the logged-in user."""
	t = (text or "").strip()
	if t.lower() in ME_WORDS:
		rows = frappe.get_list(
			"Employee", filters={"user_id": frappe.session.user},
			fields=["name", "employee_name"], limit=1,
		)
		if not rows:
			return None, {"error": "No Employee is linked to your user"}
		return rows[0], None

	or_f = {"name": ["like", f"%{t}%"], "employee_name": ["like", f"%{t}%"]}
	rows = frappe.get_list(
		"Employee", filters={"status": "Active"}, or_filters=or_f,
		fields=["name", "employee_name"], limit=5,
	) or frappe.get_list("Employee", or_filters=or_f, fields=["name", "employee_name"], limit=5)
	if not rows:
		return None, {"error": f"No employee found matching '{t}'"}
	if len(rows) > 1:
		return None, {
			"error": "Multiple employees matched, ask the user to confirm which one",
			"matches": [f"{r.employee_name} ({r.name})" for r in rows],
		}
	return rows[0], None


def _optional_employee(text):
	if not text:
		return None, None
	return _resolve_employee(text)


def get_employee_info(employee):
	emp, err = _resolve_employee(employee)
	if err:
		return err
	d = frappe.get_list(
		"Employee", filters={"name": emp.name},
		fields=["name", "employee_name", "gender", "date_of_birth", "designation", "department",
		        "branch", "company", "status", "date_of_joining", "reports_to", "employment_type",
		        "cell_number", "company_email", "personal_email"],
		limit=1,
	)[0]
	return d


def get_birthdays(month=None):
	from frappe.utils import getdate

	today = getdate(nowdate())
	month = int(month or today.month)
	rows = frappe.get_list(
		"Employee", filters={"status": "Active", "date_of_birth": ["is", "set"]},
		fields=["name", "employee_name", "date_of_birth", "department"], limit=5000,
	)
	out = []
	for r in rows:
		dob = getdate(r.date_of_birth)
		if dob.month == month:
			out.append({
				"employee": r.employee_name, "employee_id": r.name, "department": r.department,
				"birthday": f"{dob.day:02d}-{dob.month:02d}",
				"is_today": dob.month == today.month and dob.day == today.day,
			})
	out.sort(key=lambda x: x["birthday"])
	return {"month": month, "count": len(out), "birthdays": out}


def get_employee_count():
	rows = frappe.get_list("Employee", filters={"status": "Active"}, fields=["department"], limit=5000)
	by_dept = {}
	for r in rows:
		k = r.department or "No Department"
		by_dept[k] = by_dept.get(k, 0) + 1
	return {"total_active": len(rows), "by_department": by_dept}


def get_attendance_summary(employee, from_date, to_date):
	emp, err = _resolve_employee(employee)
	if err:
		return err
	rows = frappe.get_list(
		"Attendance",
		filters={"employee": emp.name, "docstatus": 1, "attendance_date": ["between", [from_date, to_date]]},
		fields=["status"], limit=500,
	)
	counts = {}
	for r in rows:
		counts[r.status] = counts.get(r.status, 0) + 1
	return {
		"employee": emp.employee_name, "employee_id": emp.name, "from": from_date, "to": to_date,
		"days_marked": len(rows), "by_status": counts,
	}


def get_attendance_on_date(date=None):
	date = date or nowdate()
	rows = frappe.get_list(
		"Attendance", filters={"attendance_date": date, "docstatus": 1},
		fields=["employee", "employee_name", "status"], limit=2000,
	)
	counts, absent = {}, []
	for r in rows:
		counts[r.status] = counts.get(r.status, 0) + 1
		if r.status == "Absent":
			absent.append({"employee": r.employee_name, "employee_id": r.employee})
	return {
		"date": date, "marked": len(rows), "by_status": counts,
		"absent_employees": absent[:50],
		"note": "Only employees whose attendance has been marked are counted",
	}


def get_leave_balance(employee, date=None):
	emp, err = _resolve_employee(employee)
	if err:
		return err
	date = date or nowdate()
	try:  # official hrms function
		from hrms.hr.doctype.leave_application.leave_application import get_leave_details

		d = get_leave_details(emp.name, date)
		return {"employee": emp.employee_name, "employee_id": emp.name, "as_on": date, "balances": d.get("leave_allocation", {})}
	except Exception:
		pass
	# fallback: allocation - approved leaves
	allocs = frappe.get_list(
		"Leave Allocation",
		filters={"employee": emp.name, "docstatus": 1, "from_date": ["<=", date], "to_date": [">=", date]},
		fields=["leave_type", "total_leaves_allocated", "from_date", "to_date"], limit=50,
	)
	out = {}
	for a in allocs:
		taken = frappe.get_list(
			"Leave Application",
			filters={"employee": emp.name, "leave_type": a.leave_type, "docstatus": 1, "status": "Approved",
			         "from_date": [">=", a.from_date], "to_date": ["<=", a.to_date]},
			fields=["total_leave_days"], limit=500,
		)
		used = sum(t.total_leave_days for t in taken)
		out[a.leave_type] = {
			"total_leaves": a.total_leaves_allocated, "leaves_taken": used,
			"remaining_leaves": a.total_leaves_allocated - used,
		}
	return {"employee": emp.employee_name, "employee_id": emp.name, "as_on": date, "balances": out}


def get_leave_applications(employee=None, status=None, count=10):
	emp, err = _optional_employee(employee)
	if err:
		return err
	filters = {"docstatus": ["<", 2]}
	if emp:
		filters["employee"] = emp.name
	if status:
		filters["status"] = status
	rows = frappe.get_list(
		"Leave Application", filters=filters,
		fields=["name", "employee_name", "leave_type", "from_date", "to_date", "total_leave_days", "status"],
		order_by="posting_date desc, creation desc", limit=max(1, min(int(count or 10), 30)),
	)
	return {"count": len(rows), "applications": rows}


def get_last_salary_slips(employee, count=1):
	emp, err = _resolve_employee(employee)
	if err:
		return err
	rows = frappe.get_list(
		"Salary Slip", filters={"employee": emp.name, "docstatus": 1},
		fields=["name", "start_date", "end_date", "gross_pay", "total_deduction", "net_pay", "currency"],
		order_by="end_date desc", limit=max(1, min(int(count or 1), 6)),
	)
	if not rows:
		return {"error": f"No salary slip found for {emp.employee_name} (or you lack permission)"}
	return {"employee": emp.employee_name, "employee_id": emp.name, "salary_slips": rows}


def get_expense_claims(employee=None, approval_status=None, count=10):
	emp, err = _optional_employee(employee)
	if err:
		return err
	filters = {"docstatus": ["<", 2]}
	if emp:
		filters["employee"] = emp.name
	if approval_status:
		filters["approval_status"] = approval_status
	rows = frappe.get_list(
		"Expense Claim", filters=filters,
		fields=["name", "employee_name", "posting_date", "total_claimed_amount",
		        "total_sanctioned_amount", "approval_status", "status"],
		order_by="posting_date desc, creation desc", limit=max(1, min(int(count or 10), 30)),
	)
	return {"count": len(rows), "claims": rows}


HR_FUNCS = {
	"get_employee_info": get_employee_info,
	"get_birthdays": get_birthdays,
	"get_employee_count": get_employee_count,
	"get_attendance_summary": get_attendance_summary,
	"get_attendance_on_date": get_attendance_on_date,
	"get_leave_balance": get_leave_balance,
	"get_leave_applications": get_leave_applications,
	"get_last_salary_slips": get_last_salary_slips,
	"get_expense_claims": get_expense_claims,
}
