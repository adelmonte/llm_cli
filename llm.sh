#!/usr/bin/env bash

# Configuration
API_BASE="http://192.168.0.1:8080/api"
API_KEY="sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
MODEL="qwen/qwen3-14b:free"
DISTRO=$(grep '^ID=' /etc/os-release | cut -d'=' -f2 | tr -d '"')
SYSTEM_PROMPT="Current context: $(date '+%b %d %Y') | Distro: $DISTRO | Shell: ${SHELL##*/} | Editor: ${EDITOR:-vi}

IMPORTANT: Use the context information above to answer questions when possible. Only run commands when you need information NOT already provided in the context.

To run system commands when needed, use this exact format: [RUN:your_command_here]

Examples:
- Check date: [RUN:date]
- Current directory: [RUN:pwd]
- List files: [RUN:ls -la]
- Chain commands: [RUN:date && whoami]

Run ONLY ONE command per user request. After receiving command output, provide a helpful response but DO NOT run additional verification commands.

Always assume bash. Before running any command, consider if you can answer using the context provided above."

# Check for markdown renderer
if command -v bat &>/dev/null; then
    HAS_RENDER=true
    RENDER_CMD="bat -l md --style=plain --paging=never --color=always"
elif command -v mdcat &>/dev/null; then
    HAS_RENDER=true
    RENDER_CMD="mdcat"
else
    HAS_RENDER=false
fi

# Conversation history
declare -a MESSAGES=()

# Initialize with system prompt if set
if [[ -n "$SYSTEM_PROMPT" ]]; then
    MESSAGES+=("{\"role\":\"system\",\"content\":$(echo "$SYSTEM_PROMPT" | jq -Rs .)}")
fi

# Spinner animation
spin() {
    local chars="◜◝◞◟"
    while :; do
        for (( i=0; i<${#chars}; i++ )); do
            printf "\r\033[2K${chars:$i:1} "
            sleep 0.08
        done
    done
}

# Typewriter effect for output
typewriter() {
    local delay="${1:-0.005}"
    
    # Set trap for cancellation
    trap 'echo; return 1' INT
    
    while IFS= read -r line; do
        for (( i=0; i<${#line}; i++ )); do
            printf "%s" "${line:$i:1}"
            sleep "$delay"
        done
        printf "\n"
    done
    
    # Remove trap
    trap - INT
}

# Interactive model selection with fzf
list_models() {
    if ! command -v fzf &>/dev/null; then
        echo "fzf not found. Install it first."
        return
    fi
    
    models=$(curl -s "$API_BASE/v1/models" \
        -H "Authorization: Bearer $API_KEY" | \
        jq -r '.data[] | "\(.id)\t\(.name)"')
    
    selected=$(echo "$models" | \
        fzf --height=40% --reverse --prompt="Select model: " \
            --delimiter='\t' \
            --with-nth=2 | \
        cut -f1)
    
    if [[ -n "$selected" ]]; then
        MODEL="$selected"
        echo -e "\n\033[1;32m✓ Switched to: $MODEL\033[0m\n"
        sleep 1
        return 0
    else
        return 1
    fi
}

# Build JSON array of messages
build_messages() {
    local json="["
    for msg in "${MESSAGES[@]}"; do
        [[ -n "$json" && "$json" != "[" ]] && json+=","
        json+="$msg"
    done
    json+="]"
    echo "$json"
}

# Display greeter with model info
show_greeter() {
    echo -e "\033[1;36m⚡ $MODEL\033[0m"
    echo -e "\033[90m/models | /clear | /exit | ctrl+l to clear | ctrl+d to exit | ctrl+c to cancel\033[0m\n"
}

# Clear conversation history
clear_conversation() {
    MESSAGES=()
    # Re-add system prompt if set
    if [[ -n "$SYSTEM_PROMPT" ]]; then
        MESSAGES+=("{\"role\":\"system\",\"content\":$(echo "$SYSTEM_PROMPT" | jq -Rs .)}")
    fi
    clear
    show_greeter
    echo -e "\033[1;33mConversation cleared\033[0m\n"
}

# Execute command with user confirmation
execute_command() {
    local cmd="$1"
    
    # Ask for confirmation with colored prompt
    echo -e "\033[1;33m🔧 Command:\033[0m $cmd" >&2
    echo -en "\033[1;32m[Y]es\033[0m / \033[1;31m[n]o\033[0m / \033[1;36m[e]dit\033[0m ? " >&2
    
    read -r reply
    echo >&2
    
    case "$reply" in
        [Yy]|"")
            # Execute command
            local output
            output=$(bash -c "$cmd" 2>&1)
            local exit_code=$?
            
            if [[ $exit_code -ne 0 ]]; then
                echo "[Command failed with exit code $exit_code]"
                echo "$output"
                return 1
            else
                echo "$output"
                return 0
            fi
            ;;
        [Ee])
            # Let user edit the command
            echo -en "\033[1;36mEdit command:\033[0m " >&2
            read -e -i "$cmd" edited_cmd
            if [[ -n "$edited_cmd" ]]; then
                execute_command "$edited_cmd"
                return $?
            else
                return 2  # Special return code for cancelled edit
            fi
            ;;
        *)
            return 2  # Special return code for user cancellation
            ;;
    esac
}

# Extract command from [RUN:...] tag
extract_run_command() {
    local content="$1"
    
    # Look for [RUN: or [RUN (with optional whitespace)
    if [[ "$content" =~ \[RUN:?[[:space:]]* ]]; then
        # Find the position after [RUN: or [RUN 
        local start="${content#*\[RUN}"
        start="${start#:}"
        start="${start##[[:space:]]}"
        
        # Now find the matching ] - look for the last ] in the content
        # This handles commands with ] characters inside them
        local cmd=""
        local bracket_count=0
        local in_run=false
        local i=0
        
        while [ $i -lt ${#content} ]; do
            local char="${content:$i:1}"
            
            if ! $in_run && [[ "${content:$i:4}" == "[RUN" ]]; then
                in_run=true
                # Skip past [RUN: or [RUN 
                while [[ $i -lt ${#content} && "${content:$i:1}" != ":" && "${content:$i:1}" != " " ]]; do
                    ((i++))
                done
                if [[ "${content:$i:1}" == ":" ]]; then
                    ((i++))
                fi
                # Skip whitespace
                while [[ $i -lt ${#content} && "${content:$i:1}" == " " ]]; do
                    ((i++))
                done
                continue
            fi
            
            if $in_run; then
                if [[ "$char" == "]" ]]; then
                    # Check if this might be the closing bracket
                    # Simple heuristic: if we hit a ] and the next char is whitespace or newline or end, it's probably the close
                    local next_char="${content:$((i+1)):1}"
                    if [[ -z "$next_char" || "$next_char" == $'\n' || "$next_char" == " " ]]; then
                        echo "$cmd"
                        return 0
                    fi
                fi
                cmd+="$char"
            fi
            
            ((i++))
        done
        
        # If we got here, return what we have
        echo "$cmd"
        return 0
    fi
    
    return 1
}

# Make API call and handle tool execution
make_api_call() {
    local messages_json=$(build_messages)
    local recursive="${1:-false}"
    
    START=$(date +%s.%N)
    
    # Start spinner
    { spin & } 2>/dev/null
    SPIN_PID=$!
    
    # Set trap for cancellation
    CANCELLED=false
    trap 'kill $SPIN_PID 2>/dev/null; wait $SPIN_PID 2>/dev/null; printf "\r\033[2K"; echo; CANCELLED=true' INT
    
    # API request
    response=$(curl -s "$API_BASE/v1/chat/completions" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d @- <<EOF
{
    "model": "$MODEL",
    "messages": $messages_json,
    "stream": false
}
EOF
    )
    
    # Remove trap
    trap - INT
    
    # Stop spinner
    if ! $CANCELLED; then
        { kill $SPIN_PID 2>/dev/null; wait $SPIN_PID 2>/dev/null; } 2>/dev/null
        printf "\r\033[2K"
    fi
    SPIN_PID=""
    
    # Skip if cancelled
    if $CANCELLED; then
        return 1
    fi
    
    END=$(date +%s.%N)
    ELAPSED=$(echo "$END - $START" | bc)
    
    # Parse response
    TOKENS=$(echo "$response" | jq -r '.usage.total_tokens // 0')
    content=$(echo "$response" | jq -r '.choices[0].message.content')
    
    # Check if response contains a command to run
    local command
    command=$(extract_run_command "$content")
    
    if [[ -n "$command" ]]; then
        # Trim leading/trailing whitespace
        command="${command#"${command%%[![:space:]]*}"}"
        command="${command%"${command##*[![:space:]]}"}"
        
        # Execute command (with confirmation)
        local cmd_output
        cmd_output=$(execute_command "$command")
        local exec_status=$?
        
        # Handle cancellation (user said no or cancelled edit)
        if [[ $exec_status -eq 2 ]]; then
            # Remove the user's unanswered question from history
            unset 'MESSAGES[${#MESSAGES[@]}-1]'
            return 0
        fi
        
        # Add assistant's message to history
        MESSAGES+=("{\"role\":\"assistant\",\"content\":$(echo "$content" | jq -Rs .)}")
        
        # Handle command failure
        if [[ $exec_status -ne 0 ]]; then
            MESSAGES+=("{\"role\":\"user\",\"content\":\"Command failed.\"}")
            make_api_call true
            return $?
        fi
        
        # Show command output
        echo -e "\033[0;90m$cmd_output\033[0m\n"
        
        # Add command output as user message
        MESSAGES+=("{\"role\":\"user\",\"content\":$(echo "Command output:\n$cmd_output" | jq -Rs .)}")
        
        # Get final response from AI (but don't allow more commands)
        make_api_call true
        return $?
    fi
    
    # Display normal response
    if [[ -n "$content" && "$content" != "null" ]]; then
        # If this is a recursive call and AI tries to run another command, strip it out
        if [[ "$recursive" == "true" && "$content" =~ \[RUN: ]]; then
            # Just display the response without the command tag
            content=$(echo "$content" | sed 's/\[RUN:[^\]]*\]//g')
        fi
        
        MESSAGES+=("{\"role\":\"assistant\",\"content\":$(echo "$content" | jq -Rs .)}")
        
        if $HAS_RENDER; then
            echo "$content" | $RENDER_CMD | typewriter 0.005
        else
            echo "$content" | typewriter 0.005
        fi
        
        # Display stats
        if [[ "$TOKENS" =~ ^[0-9]+$ ]]; then
            COLS=$(tput cols)
            STATS=$(printf "%.2fs | %d tokens" "$ELAPSED" "$TOKENS")
            STATS_LEN=${#STATS}
            PADDING=$((COLS - STATS_LEN))
            printf "%${PADDING}s\033[0;90m%s\033[0m\n" "" "$STATS"
        fi
        echo
    fi
    
    return 0
}

# Hide control character echo
OLD_STTY=$(stty -g)
stty -echoctl

# Restore terminal on exit
cleanup() {
    stty "$OLD_STTY"
}
trap cleanup EXIT

# Display greeter
clear
show_greeter

# Main loop
while true; do
    bind -x '"\C-l": clear_conversation' 2>/dev/null || true
    
    # Read user input with history support
    input=""
    while [[ -z "$input" ]]; do
        read -e -p $'\001\033[1;36m\002> \001\033[0m\002' input
        read_status=$?
        
        # Add to readline history
        if [[ -n "$input" && $read_status -eq 0 ]]; then
            history -s "$input"
        fi
        
        # Handle Ctrl+D (EOF)
        if [[ $read_status -eq 1 ]]; then
            echo
            exit 0
        elif [[ $read_status -gt 128 ]]; then
            continue
        fi
    done
    
    # Handle commands
    if [[ "$input" == "/models" ]]; then
        if list_models; then
            clear
            show_greeter
        else
            echo
        fi
        continue
    fi
    
    if [[ "$input" == "/clear" ]]; then
        clear_conversation
        continue
    fi
    
    if [[ "$input" == "/exit" ]]; then
        echo
        exit 0
    fi
    
    # Add user message
    MESSAGES+=("{\"role\":\"user\",\"content\":$(echo "$input" | jq -Rs .)}")
    
    # Make API call (handles tool execution automatically)
    make_api_call false
done
