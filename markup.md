sequenceDiagram
    autonumber
    participant J as Jenkins (Python Script)
    participant V as Venafi TPP (WebSDK)
    participant S as ServiceNow (Table API)

    Note over J: Trigger: Weekly Cron (Sunday 00:00)
    
    %% Authentication & Setup
    J->>J: Load Env Vars & Secrets
    
    %% Step 1: Retrieve Expiring Certs
    Note over J, V: PHASE 1: DISCOVERY
    J->>V: GET /vedsdk/Certificates<br/>(Filter: ValidTo < Now+60d, Limit: 1000)
    V-->>J: JSON Response (List of Certificate GUIDs)

    loop For Each Certificate
        %% Step 2: Get Details & Metadata
        J->>V: GET /vedsdk/Certificates/{GUID}
        V-->>J: JSON (Custom Fields: OwnerEmail, SN_Group, Env)
        
        J->>J: Parse "ValidTo" date<br/>Calculate "DaysRemaining"
        
        %% Step 3: Resolve ServiceNow Group
        J->>S: GET /sys_user_group<br/>(Query: name={SN_Group})
        S-->>J: JSON (sys_id for assignment)
        
        %% Step 4: Check for Existing Ticket
        Note over J, S: PHASE 2: TICKET MANAGEMENT
        J->>S: GET /change_task<br/>(Query: active=true AND short_description LIKE {Cert_CN})
        S-->>J: JSON Result (Task details or Empty)

        alt No Active Task Found (Create Workflow)
            J->>J: Check: Is DaysRemaining < 60?
            J->>S: POST /change_task
            Note right of J: Payload:<br/>Priority: 3 (Low)<br/>AssignGroup: {sys_id}<br/>State: 1 (Open)
            S-->>J: HTTP 201 Created (New Task sys_id)
            
        else Active Task Exists (Escalation Workflow)
            J->>J: Check Task State (Is it "In Progress"?)
            
            opt Task State != In Progress
                
                alt Critical Escalation (< 7 Days)
                    J->>J: Logic: Days < 7 AND Priority != 1
                    J->>S: PATCH /change_task/{task_sys_id}
                    Note right of J: Payload:<br/>Priority: 1 (High)<br/>WorkNotes: "CRITICAL Escalation"
                    S-->>J: HTTP 200 OK
                    
                else Medium Escalation (< 30 Days)
                    J->>J: Logic: Days < 30 AND Priority != 2
                    J->>S: PATCH /change_task/{task_sys_id}
                    Note right of J: Payload:<br/>Priority: 2 (Medium)<br/>WorkNotes: "Escalation: <30 Days"
                    S-->>J: HTTP 200 OK
                end
                
            end
        end
    end

    Note over J: Job Complete
