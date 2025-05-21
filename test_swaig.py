import requests
import json

def test_swaig(function, arguments, test_name):
    print(f"\n=== Testing {test_name} ===")
    data = {
        "function": function,
        "arguments": arguments
    }
    print(f"Request data: {json.dumps(data, indent=2)}")
    
    try:
        response = requests.post(
            'http://127.0.0.1:8080/swaig',
            json=data,
            headers={'Content-Type': 'application/json'}
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print(f"Response Body: {json.dumps(response.json(), indent=2)}")
        
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to the server. Is it running?")
    except requests.exceptions.RequestException as e:
        print(f"Error: {str(e)}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON response. Raw response: {response.text}")
    
    print("=" * 50)

# Test cases
test_cases = [
    {
        "name": "Check Balance Test",
        "function": "check_balance",
        "arguments": {
            "customer_id": "1"
        }
    },
    {
        "name": "Make Payment Test",
        "function": "make_payment",
        "arguments": {
            "customer_id": "1",
            "amount": 50.00
        }
    },
    {
        "name": "Check Modem Status Test",
        "function": "check_modem_status",
        "arguments": {
            "customer_id": "1"
        }
    },
    {
        "name": "Schedule Appointment Test",
        "function": "schedule_appointment",
        "arguments": {
            "customer_id": "1",
            "type": "installation",
            "date": "2024-05-25"
        }
    },
    {
        "name": "Swap Modem Test",
        "function": "swap_modem",
        "arguments": {
            "customer_id": "1",
            "date": "2024-05-25"
        }
    },
    {
        "name": "Invalid Intent Test",
        "function": "invalid_intent",
        "arguments": {
            "customer_id": "1"
        }
    },
    {
        "name": "Missing Customer ID Test",
        "function": "check_balance",
        "arguments": {}
    }
]

# Run all test cases
print("\nStarting /swaig endpoint tests...")
for test_case in test_cases:
    test_swaig(test_case["function"], test_case["arguments"], test_case["name"]) 