# Supreme Utility - AI Assistant (multi-provider)

1. Copy/merge the `supreme_utility/` folder from this zip into your bench app:
   apps/supreme_utility/supreme_utility/
   NOTE: the DocType and page live in the module folder `supreme_utility/supreme_utility/`.
   If your app's module is not named "Supreme Utility" (check
   apps/supreme_utility/supreme_utility/modules.txt), copy them into that module's folder
   and change the "module" value in the three JSON files.
2. bench --site yoursite migrate && bench build && bench restart
   (in development: stop `bench start` and run it again)
3. Open "AI Assistant Settings" in Desk -> add a row to the Providers table
   (Provider, Model, API Key, Default) -> Save.
4. Open /app/ai-assistant and choose the AI from the dropdown.

Adding a new AI: add a class in providers.py, register it in PROVIDERS, and add an option to the
'provider' Select of AI Provider Credential.
For OpenAI-compatible APIs just use "OpenAI Compatible" + Base URL.

Supported questions: Sales (bills, orders, deliveries, payments, outstanding, item rates,
sales summary) and HRMS (employee info, birthdays, attendance, leave balance and applications,
salary slips, expense claims, headcount).

## Questions you can ask

STOCK
- Current stock of item Supreme PVC Pipe 1 inch / warehouse-wise stock
- Which items are low on stock? / Items with stock below 50
- Pending orders for item X (how much is still to deliver, which customers)
- Incoming stock of item X (pending purchase orders)
- Selling price / standard rate of item X

SALES
- Last bill / last sales order / last delivery / last payment of customer X
- Last 5 rates of item A for customer X
- Sales summary from 1 Nov to 10 Nov
- How much of item X did we sell this month? Who bought the most?
- Top 10 selling items this month
- All pending sales orders / pending orders of customer X

PURCHASE
- Pending purchase orders / pending purchase orders of supplier X
- Last purchase bill of supplier X
- How much do we owe supplier X?

ACCOUNTS
- Who has not paid yet? Overdue invoices
- Total outstanding of customer X
- Total receivables and top customers who owe us
- Total payables and top suppliers we owe

HR (HRMS)
- Employee details, birthdays, headcount by department
- Attendance of an employee / who is absent today
- Leave balance, pending leave applications
- Last salary slip, expense claims
# supreme_utility
