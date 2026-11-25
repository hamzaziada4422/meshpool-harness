
### mp-on-pi, request 1

53 tools, 27,681 chars of schema. 15 largest tools (chars): meshpool_office__make_deck 1884, edit 1179, grep 1127, meshpool_office__edit_office_file 1036, meshpool_assistant__schedule_task 957, meshpool_office__add_slides 939, meshpool_office__find_icon 935, meshpool_office__read_document 902, meshpool_web__web_research 873, meshpool_assistant__run_subtasks 805, meshpool_office__check_deck 744, read 684, meshpool_web__web_search 648, meshpool_assistant__memory_save 637, find 616. Other body keys: {'model': 21, 'stream': 4, 'stream_options': 23, 'store': 5, 'max_completion_tokens': 5}

| component | chars | ~tokens | source |
|---|---:|---:|---|
| system: Pi preamble + <tools> + <rules> | 1,097 | 274 | Pi 1.5.2 built-in prompt (vendor/pi), 7 tools from settings defaultTools harnesses/pi_rpc.py:150 |
| system: Pi <docs> block | 1,301 | 325 | Pi 1.5.2 built-in prompt |
| system: Pi <skills> header | 273 | 68 | Pi 1.5.2 built-in prompt |
| system: Pi <available_skills> list | 58,549 | 14,637 | 130 skills copied into <PI_HOME>/skills/meshpool by pi_rpc.py:156 -> skills.py:380 sync_to (app/defaults/skills) |
| system: cwd | 56 | 14 | Pi |
| user msg: user task | 254 | 63 | testkit/bench3/tasks.py xlsx_edit |
| user msg: capability note [Meshpool] This message asks... | 703 | 175 | capabilities.py:100 note(), added at ui/agent_api.py:837 |
| user msg: Today line | 65 | 16 | assistant.py:367 context_block (first message) |
| user msg: HONESTY text | 453 | 113 | assistant.py:15 |
| user msg: PLAIN text | 832 | 208 | assistant.py:45 |
| user msg: Notes from Meshpool (+ENGINES) | 1,563 | 390 | assistant.py:431, ENGINES assistant.py:23 |
| tools: built-in tools (7) | 5,051 | 1,262 | Pi built-ins (read, bash, edit, write, grep, find, ls) |
| tools: MCP meshpool_assistant (9) | 4,094 | 1,023 | agent/assistant_mcp_server.py:72-104 TOOLS |
| tools: MCP meshpool_tools (3) | 854 | 213 | agent/demo_mcp_server.py |
| tools: MCP meshpool_web (3) | 2,103 | 525 | agent/web_mcp_server.py |
| tools: MCP browser (15) | 4,641 | 1,160 | agent/browser_mcp_server.py:534 TOOLS |
| tools: MCP meshpool_office (16) | 10,938 | 2,734 | agent/office_mcp_server.py:726 TOOLS |
| **sum of components** | **92,827** | **23,206** | request body 93,848 bytes = ~23,462 tokens |

### mp-work, request 1 of the agent

62 tools. 15 largest (chars): meshpool-office__make_deck 1915, delegate_task 1422, office_task 1251, ask_user 1246, edit 1074, meshpool-office__edit_office_file 1067, meshpool-assistant__schedule_task 991, meshpool-office__add_slides 970, meshpool-office__find_icon 966, meshpool-office__read_document 933, grep 792, meshpool-office__check_deck 775, web_research 714, meshpool-assistant__memory_save 671, notebook_edit 652

| component | chars | ~tokens | source |
|---|---:|---:|---|
| system: WORK_SYSTEM | 7,990 | 1,997 | agent/core.py:242 |
| system: V3_RULES | 506 | 126 | agent/core.py:177, appended 972 |
| system: E2E_RULES | 2,173 | 543 | agent/core.py:188, appended 974 |
| system: skills index | 226 | 56 | skills.py:242 catalog_text, appended core.py:983 |
| system: PLAIN | 832 | 208 | assistant.py:45, appended core.py:993 |
| system: HONESTY | 451 | 112 | assistant.py:15, appended core.py:993 |
| user msg: user task | 254 | 63 | testkit/bench3/tasks.py xlsx_edit |
| user msg: capability note [Meshpool] This message asks... | 703 | 175 | capabilities.py:100 note(), added at ui/agent_api.py:837 |
| user msg: Today line | 65 | 16 | assistant.py:367 context_block (first message) |
| user msg: HONESTY text | 453 | 113 | assistant.py:15 |
| user msg: PLAIN text | 832 | 208 | assistant.py:45 |
| user msg: Notes from Meshpool (+ENGINES) | 1,565 | 391 | assistant.py:431, ENGINES assistant.py:23 |
| user msg: <environment_snapshot> | 430 | 107 | agent/core.py:1320 (_env_snapshot 537) |
| tools: built-in tools (20) | 12,594 | 3,148 | core/tools.py:239 SCHEMAS (+ask.py, delegate.py:167, core.py office_task) ; names in agent/core.py:57-66 |
| tools: MCP meshpool_assistant (8) | 3,561 | 890 | agent/assistant_mcp_server.py:72-104 TOOLS |
| tools: MCP browser (15) | 4,986 | 1,246 | agent/browser_mcp_server.py:534 TOOLS |
| tools: MCP meshpool_tools (3) | 944 | 236 | agent/demo_mcp_server.py |
| tools: MCP meshpool_office (16) | 11,434 | 2,858 | agent/office_mcp_server.py:726 TOOLS |
| **sum of components** | **49,999** | **12,499** | request body 52,053 bytes = ~13,013 tokens |

### mp-cli, request 1 of the agent

60 tools. 15 largest (chars): meshpool-office__make_deck 1915, delegate_task 1422, edit 1074, meshpool-office__edit_office_file 1067, meshpool-assistant__schedule_task 991, meshpool-office__add_slides 970, meshpool-office__find_icon 966, meshpool-office__read_document 933, grep 792, meshpool-office__check_deck 775, web_research 714, meshpool-assistant__memory_save 671, notebook_edit 652, meshpool-office__check_workbook 634, meshpool-office__slide_layouts 626

| component | chars | ~tokens | source |
|---|---:|---:|---|
| system: SYSTEM (code agent) | 3,569 | 892 | agent/core.py:143 |
| system: V3_RULES | 506 | 126 | agent/core.py:177, appended 972 |
| system: E2E_RULES | 2,173 | 543 | agent/core.py:188, appended 974 |
| system: skills index | 226 | 56 | skills.py:242 catalog_text, appended core.py:983 |
| system: PLAIN | 832 | 208 | assistant.py:45, appended core.py:993 |
| system: HONESTY | 451 | 112 | assistant.py:15, appended core.py:993 |
| user msg: user task | 254 | 63 | testkit/bench3/tasks.py xlsx_edit |
| user msg: Today line | 65 | 16 | assistant.py:367 context_block (first message) |
| user msg: HONESTY text | 453 | 113 | assistant.py:15 |
| user msg: PLAIN text | 832 | 208 | assistant.py:45 |
| user msg: Notes from Meshpool (+ENGINES) | 1,565 | 391 | assistant.py:431, ENGINES assistant.py:23 |
| user msg: <environment_snapshot> | 430 | 107 | agent/core.py:1320 (_env_snapshot 537) |
| tools: built-in tools (18) | 10,097 | 2,524 | core/tools.py:239 SCHEMAS (+ask.py, delegate.py:167, core.py office_task) ; names in agent/core.py:57-66 |
| tools: MCP meshpool_assistant (8) | 3,561 | 890 | agent/assistant_mcp_server.py:72-104 TOOLS |
| tools: MCP browser (15) | 4,986 | 1,246 | agent/browser_mcp_server.py:534 TOOLS |
| tools: MCP meshpool_tools (3) | 944 | 236 | agent/demo_mcp_server.py |
| tools: MCP meshpool_office (16) | 11,434 | 2,858 | agent/office_mcp_server.py:726 TOOLS |
| **sum of components** | **42,378** | **10,594** | request body 44,241 bytes = ~11,060 tokens |

### pi, request 1

15 largest tools (chars): edit 1252, read 694, bash 557, write 445

| component | chars | ~tokens | source |
|---|---:|---:|---|
| msg0.system (You are an expert coding assistant operating insid...) | 2,327 | 581 |  |
| msg1.user (In inventory.xlsx, add a Value column (qty times u...) | 251 | 62 |  |
| tools (4) | 2,948 | 737 |  |
| **sum of components** | **5,526** | **1,381** | request body 5,796 bytes = ~1,449 tokens |

### pi152, request 1

15 largest tools (chars): edit 1179, read 684, bash 543, write 430

| component | chars | ~tokens | source |
|---|---:|---:|---|
| msg0.system (You are a coding assistant running inside pi, a co...) | 2,338 | 584 |  |
| msg1.user (In inventory.xlsx, add a Value column (qty times u...) | 251 | 62 |  |
| tools (4) | 2,836 | 709 |  |
| **sum of components** | **5,425** | **1,356** | request body 5,700 bytes = ~1,425 tokens |

### codex, request 1

13 tools. 15 largest tools (chars): multi_agent_v1.spawn_agent 7576, update_goal 2172, multi_agent_v1.send_input 1594, exec_command 1529, request_user_input 1355, write_stdin 784, create_goal 760, multi_agent_v1.wait_agent 736, multi_agent_v1.close_agent 548, view_image 371, multi_agent_v1.resume_agent 314, get_goal 281, web_search 48

| component | chars | ~tokens | source |
|---|---:|---:|---|
| in0.developer (You are a coding agent running in the Codex CLI, a terminal-...) | 16,979 | 4,244 | Codex's own base instructions (model-family prompt) |
| in1.developer (<skills_instructions> ## Skills A skill is a set of local in...) | 2,513 | 628 | Codex skills list (system skills) |
| in2.user (<environment_context>   <cwd><scratch>/work</...) | 379 | 94 | Codex <environment_context> |
| in3.user (In inventory.xlsx, add a Value column (qty times unit cost) ...) | 251 | 62 | task |
| tools: built-in tools (13) | 18,068 | 4,517 | Codex built-ins (exec_command, write_stdin, multi_agent_v1, goal tools, view_image, ...) |
| **sum of components** | **38,190** | **9,547** | request body 40,855 bytes = ~10,213 tokens |

### mp-on-codex, request 1 (main session)

62 tools. 15 largest tools (chars): multi_agent_v1.spawn_agent 6614, update_goal 2172, meshpool_office.make_deck 1864, multi_agent_v1.send_input 1594, exec_command 1564, request_user_input 1355, meshpool_office.edit_office_file 1032, meshpool_assistant.schedule_task 934, meshpool_office.add_slides 919, meshpool_office.find_icon 915, meshpool_office.read_document 882, meshpool_web.web_research 856, write_stdin 784, meshpool_assistant.run_subtasks 782, create_goal 760

| component | chars | ~tokens | source |
|---|---:|---:|---|
| in0.developer (You are a coding agent running in the Codex CLI, a terminal-...) | 16,979 | 4,244 | Codex's own base instructions (model-family prompt) |
| in1.developer (<skills_instructions> ## Skills A skill is a set of local in...) | 2,342 | 585 | Codex skills list (system skills) |
| in2.user (<environment_context>   <cwd><scratch>/...) | 391 | 97 | Codex <environment_context> |
| user msg: user task | 254 | 63 | testkit/bench3/tasks.py xlsx_edit |
| user msg: capability note [Meshpool] This message asks... | 703 | 175 | capabilities.py:100 note(), added at ui/agent_api.py:837 |
| user msg: Today line | 65 | 16 | assistant.py:367 context_block (first message) |
| user msg: HONESTY text | 453 | 113 | assistant.py:15 |
| user msg: PLAIN text | 832 | 208 | assistant.py:45 |
| user msg: Notes from Meshpool (+ENGINES) | 1,563 | 390 | assistant.py:431, ENGINES assistant.py:23 |
| tools: built-in tools (16) | 19,012 | 4,753 | Codex built-ins (exec_command, write_stdin, multi_agent_v1, goal tools, view_image, ...) |
| tools: MCP browser (15) | 4,461 | 1,115 | agent/browser_mcp_server.py:534, via acp.py:159 mcp_servers_for_acp |
| tools: MCP meshpool_assistant (9) | 3,887 | 971 | agent/assistant_mcp_server.py:72-104, via acp.py:159 |
| tools: MCP meshpool_office (16) | 10,634 | 2,658 | agent/office_mcp_server.py:726 TOOLS via harnesses/acp.py:200 office_server, acp.py:627 |
| tools: MCP meshpool_tools (3) | 797 | 199 | agent/demo_mcp_server.py, via acp.py:159 |
| tools: MCP meshpool_web (3) | 2,052 | 513 | agent/web_mcp_server.py, via acp.py:159 |
| **sum of components** | **64,425** | **16,106** | request body 67,918 bytes = ~16,979 tokens |

### mp-on-codex, request 3 = the second Codex session codex-acp starts to write the chat title

3 tools. 15 largest tools (chars): functions.exec 14392, functions.wait 1356, functions.request_user_input 1355

| component | chars | ~tokens | source |
|---|---:|---:|---|
| in1.developer (You are Codex, an agent based on GPT-5. You and the user sha...) | 17,730 | 4,432 | second Codex session (title writer), model gpt-5.6-luna |
| in2.developer (<skills_instructions> ## Skills A skill is a set of local in...) | 5,862 | 1,465 | second Codex session (title writer), model gpt-5.6-luna |
| in3.user (<environment_context>   <cwd><scratch>/...) | 453 | 113 | second Codex session (title writer), model gpt-5.6-luna |
| user msg: (start) | 541 | 135 |  |
| user msg: user task | 254 | 63 | testkit/bench3/tasks.py xlsx_edit |
| user msg: capability note [Meshpool] This message asks... | 703 | 175 | capabilities.py:100 note(), added at ui/agent_api.py:837 |
| user msg: Today line | 65 | 16 | assistant.py:367 context_block (first message) |
| user msg: HONESTY text | 453 | 113 | assistant.py:15 |
| user msg: PLAIN text | 832 | 208 | assistant.py:45 |
| user msg: Notes from Meshpool (+ENGINES) | 1,563 | 390 | assistant.py:431, ENGINES assistant.py:23 |
| tools: built-in tools (3) | 17,103 | 4,275 | Codex built-ins (exec_command, write_stdin, multi_agent_v1, goal tools, view_image, ...) |
| **sum of components** | **45,559** | **11,389** | request body 48,461 bytes = ~12,115 tokens |
