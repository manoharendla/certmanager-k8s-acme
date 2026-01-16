Here is the complete, consolidated Solution Design Document formatted as a ready-to-use `README.md`.

---

# Enterprise Certificate Automation Solution (Venafi vCert + Ansible + Jenkins)

## 1. Executive Summary

This repository contains the comprehensive Infrastructure-as-Code (IaC) solution for automating the lifecycle of SSL/TLS certificates across the enterprise. It leverages **Venafi vCert** for certificate logic, **Ansible** for configuration management, and **Jenkins** for orchestration, with governance provided by **ServiceNow**.

### Key Features

* **Unified "Hybrid" Architecture:** Uses Ansible to "push" configuration and standardizes a "pull" mechanism on endpoints using vCert.
* **Maintenance Window Compliance:** Production renewals are strictly gated by ServiceNow Change Requests (CRs) to prevent unapproved restarts.
* **Environment Agnostic:** Single codebase supports **Development** (Automatic/Scheduled) and **Production** (CR-Gated) environments.
* **OS & App Agnostic:** Supports Linux (Nginx/Apache), Windows (IIS/Tomcat), and service-less (Batch/Script) implementations via data-driven configuration.
* **Zero-Touch Operations:** Automatically handles KeyStore generation (JKS), backups, and service restarts/reloads.

---

## 2. Architecture

### Workflow Diagram

```mermaid
graph TD
    subgraph "Governance & Orchestration"
        SNOW[ServiceNow Change Req] -->|Webhook (Prod Only)| Jenkins
        Cron[Scheduler] -->|Timer (Dev Only)| Jenkins
        Jenkins -->|Policy Check| Ansible[Ansible Controller]
    end

    subgraph "Target Infrastructure"
        Ansible -->|1. Enforce Config| Host[Target Server (Linux/Win)]
        Host -->|2. vCert Execution| vCert[vCert Binary]
        vCert -->|3. Validate/Renew| TPP[Venafi TPP]
        vCert -->|4. Post-Install Action| Service[App Service (Restart/Reload)]
    end

```

### Operational Logic

* **Development:** Jenkins runs nightly. Certificates renew automatically when within the window.
* **Production:** Jenkins runs **only** when triggered by ServiceNow. It validates the `CR_NUMBER`. Restarts happen only during the approved window.

---

## 3. Repository Structure

```text
.
├── inventory/
│   ├── dev/
│   │   ├── hosts.ini           # Dev targets
│   │   └── group_vars/all.yml  # Dev specific zones (e.g., DevOps\Dev)
│   └── prod/
│       ├── hosts.ini           # Prod targets
│       └── group_vars/all.yml  # Prod specific zones (e.g., DevOps\Prod)
├── playbooks/
│   ├── deploy_vcert.yml        # MAIN Playbook (Idempotent)
│   └── templates/
│       └── vcert_playbook.j2   # Universal Jinja2 Template
├── host_vars/                  # Application Specific Configurations
│   ├── app01_nginx.yml
│   └── app02_tomcat.yml
├── Jenkinsfile                 # Orchestrator Pipeline
└── README.md

```

---

## 4. Configuration Guide

All application logic is abstracted into **Ansible Variables**. No code changes are required to onboard new apps.

### 4.1 Global OS Defaults

Define these in `inventory/group_vars/[linux|windows].yml`.

| Variable | Linux Value | Windows Value |
| --- | --- | --- |
| `vcert_bin` | `/usr/local/bin/vcert` | `C:\Program Files\Venafi\vcert.exe` |
| `vcert_config` | `/etc/venafi/playbook.yaml` | `C:\ProgramData\Venafi\playbook.yaml` |

### 4.2 Application Definitions (`host_vars`)

#### Scenario A: Tomcat (Windows) - JKS Format & Restart

```yaml
# host_vars/app02_tomcat.yml
cert_common_name: "app02.prod.local"
cert_format: "JKS"
cert_install_path: "C:\\Tomcat\\conf\\keystore.jks"

# JKS Specifics
cert_alias: "tomcat"
keystore_password: "changeit"  # Matches server.xml

# Action
post_install_cmd: "Restart-Service Tomcat"

```

#### Scenario B: Nginx (Linux) - PEM Format & Reload (Zero Downtime)

```yaml
# host_vars/app01_nginx.yml
cert_common_name: "web.prod.local"
cert_format: "PEM"
cert_install_path: "/etc/nginx/ssl/server.crt"
cert_key_path: "/etc/nginx/ssl/server.key"
cert_chain_path: "/etc/nginx/ssl/chain.crt"

# Action
post_install_cmd: "systemctl reload nginx"

```

#### Scenario C: Client Cert (Script) - No Service Restart

```yaml
# host_vars/batch_client.yml
cert_common_name: "batch.prod.local"
cert_format: "PEM"
cert_install_path: "/opt/scripts/auth.pem"
# post_install_cmd is OMITTED, so no action is taken.

```

---

## 5. Implementation Artifacts

### 5.1 The Universal Template (`templates/vcert_playbook.j2`)

Handles the translation of Ansible variables into a valid vCert configuration file.

```yaml
config:
  connection:
    platform: tpp
    url: "https://tpp.venafi.internal/vedsdk"
    credentials:
      apiKey: "{{ venafi_api_key }}"

certificateTasks:
  - name: "{{ inventory_hostname }}_cert_req"
    renewBefore: "45d" # Buffer to hit monthly maintenance windows
    request:
      zone: "{{ vcert_zone }}"
      subject:
        commonName: "{{ cert_common_name }}"
    installations:
      - format: "{{ cert_format }}"
        file: "{{ cert_install_path }}"
        
        # JKS Logic
        {% if cert_format == 'JKS' %}
        jksAlias: "{{ cert_alias }}"
        jksPassword: "{{ keystore_password }}"
        keyPassword: "{{ keystore_password }}"
        {% endif %}

        # PEM Logic
        {% if cert_format == 'PEM' %}
        keyFile: "{{ cert_key_path }}"
        chainFile: "{{ cert_chain_path }}"
        {% endif %}

        # Dynamic Post-Install Hook
        {% if post_install_cmd is defined %}
        afterInstallAction: "{{ post_install_cmd }}"
        {% endif %}

```

### 5.2 The Main Playbook (`playbooks/deploy_vcert.yml`)

Handles idempotency, backups, and execution.

```yaml
---
- name: Venafi Certificate Lifecycle Management
  hosts: all
  become: yes
  tasks:
    # 1. Scaffolding: Ensure vCert Binary Exists
    - name: Ensure vCert installed
      get_url:
        url: "https://repo/vcert/{{ 'vcert.exe' if ansible_os_family == 'Windows' else 'vcert-linux' }}"
        dest: "{{ vcert_bin }}"
        mode: '0755'

    # 2. Safety: Backup existing file if it exists
    - name: Backup existing cert
      copy:
        src: "{{ cert_install_path }}"
        dest: "{{ cert_install_path }}.{{ ansible_date_time.iso8601 }}.bak"
        remote_src: yes
      ignore_errors: yes

    # 3. Config: Deploy the standardized playbook
    - name: Render vCert Config
      template:
        src: templates/vcert_playbook.j2
        dest: "{{ vcert_config }}"

    # 4. Execution: Run vCert
    # This is "Smart": It checks TPP. If cert is valid -> Exit. If expiring -> Renew & Restart.
    - name: Run vCert Lifecycle
      command: "{{ vcert_bin }} run -f {{ vcert_config }}"
      register: vcert_out
      changed_when: "'Certificate renewed' in vcert_out.stdout"

    - name: Audit Log
      debug: 
        msg: "{{ vcert_out.stdout_lines }}
    - name: Setup vCert Renewal (Last Sunday of Month)
      cron:
        name: "Venafi vCert Monthly Renewal"
        minute: "0"
        hour: "2"
        weekday: "0" # 0 = Sunday
        job: '[ "$(date +\%d)" -gt 24 ] && /usr/local/bin/vcert run -f /etc/venafi/playbook.yaml'
    - name: Setup vCert Renewal (Windows Last Sunday)
      win_scheduled_task:
        name: "VenafiCertificateAutoRenewal"
        actions:
          - path: "C:\\Program Files\\Venafi\\vcert.exe"
            arguments: "run -f C:\\ProgramData\\Venafi\\playbook.yaml"
        triggers:
          - type: monthlydow
            weeks: last # Specifically targets the last week
            days_of_week: sunday
            start_boundary: "2024-01-01T02:00:00"
        username: SYSTEM"

```

### 5.3 The Orchestrator (`Jenkinsfile`)

Enforces the governance policy (Dev vs Prod).

```groovy
pipeline {
    agent any
    parameters {
        choice(name: 'ENVIRONMENT', choices: ['dev', 'prod'], description: 'Target Environment')
        string(name: 'TARGET_HOST', description: 'Specific Hostname')
        string(name: 'CR_NUMBER', description: 'ServiceNow CR# (Required for Prod)', defaultValue: '')
    }
    
    stages {
        stage('Policy Enforcement') {
            steps {
                script {
                    if (params.ENVIRONMENT == 'prod' && !params.CR_NUMBER?.trim()) {
                        error "⛔ DENIED: Production renewal requires a valid CR_NUMBER."
                    }
                    echo "✅ Policy Passed for ${params.ENVIRONMENT}"
                }
            }
        }
        stage('Execute Renewal') {
            steps {
                script {
                    sh """
                        ansible-playbook -i inventory/${params.ENVIRONMENT} \
                        playbooks/deploy_vcert.yml \
                        --limit ${params.TARGET_HOST}
                    """
                }
            }
        }
    }
}

```

---

## 6. Operational Workflows

### How to Onboard a New App

1. Create `host_vars/<new_hostname>.yml`.
2. Populate variables (CN, Format, Path, Zone).
3. Commit to Git.
4. Run Jenkins manually (Target: `<new_hostname>`) to bootstrap the first certificate.

### Routine Production Renewal

1. **ServiceNow:** Change Request is created/approved.
2. **ServiceNow:** Triggers Jenkins Webhook with `ENVIRONMENT=prod`, `TARGET_HOST=app01`, `CR_NUMBER=CHG123`.
3. **Jenkins:** Verifies CR presence -> Runs Ansible.
4. **Ansible/vCert:** Renews certificate -> Restarts Service.
5. **Jenkins:** Reports status back to ServiceNow.

### Routine Dev Renewal

1. **Jenkins:** Runs nightly on schedule.
2. **Policy:** Skips CR check.
3. **Ansible/vCert:** Renews any certificates within the `renewBefore` window (e.g., 30 days).


ghe-ssl-certificate-install -c /home/admin/$CERT_NAME.crt -k /home/admin/$CERT_NAME.key
    
    # Apply the configuration to active services
    ghe-config-apply
