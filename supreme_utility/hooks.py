app_name = "supreme_utility"
app_title = "Supreme Utility"
app_publisher = "Atul Sah"
app_description = "For Supreme Utility Documents"
app_email = "atul.sah7@gmail.com"
app_license = "mit"

required_apps = ["erpnext", "hrms"]

PJP = "supreme_utility.supreme_utility.doctype.pjp_plan.pjp_plan"

# Only the workspace is a fixture. Custom fields and Expense Claim Types are created by install.py
# (after_install and after_migrate), so a plain `bench migrate` always repairs them.
fixtures = [
	{"dt": "Workspace", "filters": [["name", "in", ["Supreme"]]]},
]

after_install = "supreme_utility.install.after_install"
after_migrate = "supreme_utility.install.after_migrate"

doc_events = {
	"Expense Claim": {
		"on_cancel": f"{PJP}.release_pjp",
		"on_trash": f"{PJP}.release_pjp",
	},
	"Employee Advance": {
		"on_cancel": f"{PJP}.release_advance",
		"on_trash": f"{PJP}.release_advance",
	},
}

permission_query_conditions = {
	"PJP Plan": f"{PJP}.get_permission_query_conditions",
}

has_permission = {
	"PJP Plan": f"{PJP}.has_permission",
}

app_home = "/app/supreme"

add_to_apps_screen = [
	{
		"name": "supreme_utility",
		"logo": "/assets/supreme_utility/images/Supreme.png",
		"title": "Supreme",
		"route": "/app/supreme",
	}
]
