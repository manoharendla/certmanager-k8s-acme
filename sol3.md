This Solution Design Document (SDD) outlines the architecture, workflow, and technical specifications for automating Certificate Lifecycle Management (CLM) escalation using Venafi TPP, ServiceNow, and Jenkins.

---

# Solution Design Document: Automated Certificate Expiry Escalation

## 1. Executive Summary

This solution automates the tracking and escalation of expiring digital certificates. A Jenkins job scheduled to run weekly will trigger a Python script to:

1. Retrieve certificates from Venafi TPP expiring within 60 days.
2. Extract ownership metadata (Email, ServiceNow Group, Environment) from Venafi Custom Fields.
3. Cross-reference with ServiceNow to Create or Update Change Tasks (CTASKs).
4. Escalate priority (Low → Medium → High) based on proximity to expiration and lack of action.

---

## 2. High-Level Architecture

The solution consists of three main components:

* **Source of Truth (Venafi TPP):** Stores certificate inventory and ownership metadata in Custom Fields.
* **Orchestrator (Jenkins):** Schedules the job and manages secrets (API Keys/Credentials).
* **Execution Engine (Python Script):** Interfaces with Venafi WebSDK and ServiceNow Table API to execute logic.

---

## 3. Prerequisites & Configuration

### 3.1. Venafi TPP Configuration

You must define **Custom Fields** in Venafi to store metadata. These fields must be populated for the certificates.

* **Field 1:** `Owner Email` (Type: Text) - Comma-separated list of owner emails.
* **Field 2:** `SN Assignment Group` (Type: Text) - Exact name of the ServiceNow Group (e.g., "Network_Security").
* **Field 3:** `Environment` (Type: List/Text) - E.g., "PROD", "QA".

### 3.2. ServiceNow Configuration

* **Service Account:** A dedicated user with roles to `read/write` to the `change_task` and `sys_user_group` tables.
* **Table:** `change_task` (Change Task).

### 3.3. Jenkins Configuration

* **Credentials:** Store Venafi TPP Access Token and ServiceNow Credentials in Jenkins Credentials Manager.
* **Agent:** A Linux/Windows agent with Python 3.9+ and `requests` library installed.

---

## 4. Workflow Logic

The logic is divided into three "Time Buckets" based on `DaysToExpiry`:

### Phase A: Detection (< 60 Days)

1. **Query Venafi:** Find all active certificates where `ValidTo < Now + 60 Days`.
2. **Check ServiceNow:** Query `change_task` table for an **Active** task where `short_description` contains the Certificate CN.
* *If No Task Exists:* **CREATE** new CTASK.
* **Priority:** Low (3).
* **Assignment Group:** Value from Venafi `SN Assignment Group`.
* **Description:** "Certificate [CN] expires in X days. Please renew."





### Phase B: Escalation Level 1 (< 30 Days)

1. **Check ServiceNow:** If Task Exists AND State is NOT "In Progress" (or Closed).
2. **Action:** Update CTASK.
* **Priority:** Medium (2).
* **Work Notes:** "Auto-Escalation: Certificate expires in less than 30 days. Priority bumped."



### Phase C: Critical Escalation (< 7 Days)

1. **Check ServiceNow:** If Task Exists AND State is NOT "In Progress" (or Closed).
2. **Action:** Update CTASK.
* **Priority:** High (1).
* **Work Notes:** "CRITICAL: Certificate expires in less than 7 days. Priority bumped to High."



---

## 5. Technical Specifications & Data Mapping

### 5.1. Venafi WebSDK Endpoints

| Action | Endpoint | Method | Purpose |
| --- | --- | --- | --- |
| **Search** | `/vedsdk/Certificates` | `GET` | Retrieve expiring certs. Filter: `ValidTo`. |
| **Details** | `/vedsdk/Certificates/{GUID}` | `GET` | Retrieve Custom Field values for a specific cert. |

### 5.2. ServiceNow API Endpoints

| Action | Endpoint | Method | Purpose |
| --- | --- | --- | --- |
| **Find Group** | `/api/now/table/sys_user_group` | `GET` | Resolve Group Name to `sys_id`. |
| **Find Task** | `/api/now/table/change_task` | `GET` | Check for existing active tasks. |
| **Create Task** | `/api/now/table/change_task` | `POST` | Create new renewal task. |
| **Update Task** | `/api/now/table/change_task/{sys_id}` | `PATCH` | Update Priority/Work notes. |

---

## 6. Implementation Details

### 6.1. Python Script (`cert_escalation.py`)

This script encapsulates the core logic. It requires `requests` and `python-dateutil`.

```python
import requests
import json
import os
from datetime import datetime, timedelta
from dateutil import parser

# CONFIGURATION (Loaded from Env Vars)
TPP_URL = os.getenv('TPP_URL')  # e.g., https://tpp.example.com
TPP_TOKEN = os.getenv('TPP_TOKEN')
SN_URL = os.getenv('SN_URL')    # e.g., https://instance.service-now.com
SN_USER = os.getenv('SN_USER')
SN_PASS = os.getenv('SN_PASS')

# HEADERS
tpp_headers = {'Authorization': f'Bearer {TPP_TOKEN}', 'Content-Type': 'application/json'}
sn_headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

def get_servicenow_group_id(group_name):
    """Resolves Group Name to Sys_ID"""
    url = f"{SN_URL}/api/now/table/sys_user_group?sysparm_query=name={group_name}"
    response = requests.get(url, auth=(SN_USER, SN_PASS), headers=sn_headers)
    if response.status_code == 200 and response.json()['result']:
        return response.json()['result'][0]['sys_id']
    return None

def find_existing_task(cert_cn):
    """Checks for active CTASK for this cert"""
    # Query: Active=true AND Short Description contains Cert CN
    query = f"active=true^short_descriptionLIKE{cert_cn}"
    url = f"{SN_URL}/api/now/table/change_task?sysparm_query={query}"
    response = requests.get(url, auth=(SN_USER, SN_PASS), headers=sn_headers)
    if response.status_code == 200:
        results = response.json()['result']
        return results[0] if results else None
    return None

def process_certificates():
    # 1. Calculate Date Range (Now + 60 Days)
    target_date = (datetime.now() + timedelta(days=60)).isoformat()
    
    # 2. Query Venafi (Search for expiring certs)
    # Note: Using valid WebSDK syntax for filtering
    search_url = f"{TPP_URL}/vedsdk/Certificates"
    params = {'validToLess': target_date, 'limit': 1000} 
    
    print(f"Searching Venafi for certs expiring before {target_date}...")
    resp = requests.get(search_url, headers=tpp_headers, params=params)
    
    if resp.status_code != 200:
        print(f"Error fetching certs: {resp.text}")
        return

    certs = resp.json().get('Certificates', [])
    
    for cert in certs:
        guid = cert['ID']
        cn = cert['Name']
        valid_to_str = cert['ValidTo']
        valid_to_date = parser.parse(valid_to_str).replace(tzinfo=None) # Naive comparison
        days_remaining = (valid_to_date - datetime.now()).days

        # 3. Retrieve Custom Fields (Owner, Group, Env)
        details_url = f"{TPP_URL}/vedsdk/Certificates/{guid}"
        details_resp = requests.get(details_url, headers=tpp_headers)
        details = details_resp.json()
        
        # Extract Custom Fields (Adjust key names based on your TPP setup)
        custom_fields = {cf['Name']: cf['Value'] for cf in details.get('CustomFields', [])}
        owner_email = custom_fields.get('Owner Email', 'admin@example.com')
        sn_group_name = custom_fields.get('SN Assignment Group', 'ServiceDesk') # Fallback
        
        # 4. ServiceNow Logic
        existing_task = find_existing_task(cn)
        group_id = get_servicenow_group_id(sn_group_name)
        
        if not group_id:
            print(f"Warning: Group {sn_group_name} not found in SN. Skipping assignment.")
            continue

        if not existing_task:
            # CREATE NEW TASK (Phase A)
            if days_remaining < 60:
                print(f"Creating CTASK for {cn} (Expiring in {days_remaining} days)")
                payload = {
                    "short_description": f"Renew Certificate: {cn}",
                    "description": f"Owner: {owner_email}\nExpiry: {valid_to_str}\nEnvironment: {custom_fields.get('Environment')}",
                    "assignment_group": group_id,
                    "priority": "3", # Low
                    "state": "1" # Open
                }
                requests.post(f"{SN_URL}/api/now/table/change_task", auth=(SN_USER, SN_PASS), headers=sn_headers, json=payload)
        
        else:
            # UPDATE EXISTING TASK (Phase B & C)
            task_sys_id = existing_task['sys_id']
            task_state = existing_task['state'] # e.g., 2 = In Progress
            current_priority = existing_task['priority']
            
            # ServiceNow State 2 usually means "In Progress". Check your instance mappings.
            if task_state != "2": 
                new_priority = None
                
                # Logic: < 7 Days = High (1)
                if days_remaining < 7 and current_priority != "1":
                    new_priority = "1"
                    note = "ESCALATION: < 7 Days remaining."
                # Logic: < 30 Days = Medium (2)
                elif days_remaining < 30 and days_remaining >= 7 and current_priority != "2" and current_priority != "1":
                    new_priority = "2"
                    note = "ESCALATION: < 30 Days remaining."
                
                if new_priority:
                    print(f"Escalating {cn} to Priority {new_priority}")
                    update_payload = {"priority": new_priority, "work_notes": note}
                    requests.patch(f"{SN_URL}/api/now/table/change_task/{task_sys_id}", auth=(SN_USER, SN_PASS), headers=sn_headers, json=update_payload)

if __name__ == "__main__":
    process_certificates()

```

### 6.2. Jenkins Pipeline (`Jenkinsfile`)

This Declarative Pipeline runs the script.

```groovy
pipeline {
    agent any
    
    triggers {
        // Run weekly on Sunday at midnight
        cron('0 0 * * 0')
    }
    
    environment {
        // Credentials Binding (Manage Jenkins > Credentials)
        TPP_CREDS = credentials('venafi-tpp-api-token') // Username=AppId, Password=Token
        SN_CREDS  = credentials('servicenow-service-account')
        
        // Environment Variables
        TPP_URL = "https://tpp.yourcompany.com"
        SN_URL  = "https://yourcompany.service-now.com"
        TPP_TOKEN = "${TPP_CREDS_PASSWORD}"
        SN_USER   = "${SN_CREDS_USR}"
        SN_PASS   = "${SN_CREDS_PSW}"
    }
    
    stages {
        stage('Setup') {
            steps {
                sh 'pip3 install requests python-dateutil'
            }
        }
        
        stage('Run Escalation Logic') {
            steps {
                sh 'python3 cert_escalation.py'
            }
        }
    }
}

```

---

## 7. Edge Cases & Considerations

1. **Missing Custom Fields:** If a certificate lacks the `SN Assignment Group` custom field, the script has a fallback (`ServiceDesk`). Ensure data hygiene in Venafi by making these fields mandatory on the Policy folder.
2. **Duplicate Tasks:** The `find_existing_task` function matches `active=true` tasks by Name. If multiple tasks exist (rare), it picks the first one.
3. **ServiceNow API Limits:** If processing >1000 certificates, implement pagination in the Python script logic (Venafi WebSDK uses `Limit` and `Offset`).
4. **Priority Codes:** Validate the Priority Integer values in your specific ServiceNow instance (e.g., 1=Critical, 2=High, 3=Moderate, 4=Low). Adjust the script accordingly.
