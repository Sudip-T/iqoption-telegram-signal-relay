import time
from iqoptionapiv2.iqclient import IQOptionAPIClient


iq = IQOptionAPIClient()
currency = "EURUSD-op"

while True:
    try:
        if iq.connect():
            break
    except Exception as e:
        print(e)
        break


print("\n=== Connected | ACCOUNT SUMMARY ===\n")

for acc in iq.get_balances()["msg"]:
    acc_id = acc.get("id")
    acc_type = acc.get("type")
    amount = acc.get("amount")
    currency = acc.get("currency")
    tournament = acc.get("tournament_name")

    if acc_type == 2:
        print("#ID:", acc_id,"Balance:", amount,"Tournament:", tournament)
    else:
        print("#ID:", acc_id,"Balance:", amount)


def _rebuy(tournament_id:int):
    msg = {
        "name": "rebuy-in-tournament-new",
        "version": "1.0",
        "body": {
            "rebuy": 1,
            "force": True,
            "tournament_id": tournament_id
        }
    }

    iq.api.send_websocket_request('sendMessage', msg)
    return True, f"Rebuy initiated for tournament {tournament_id}"


def tournament_rebuy(tournament_user_id: int, rebuy_amount:int=None):
    _balances = iq.get_balances() # balances list including all tournament accounts
    tournament_id = None

    for item in _balances['msg']:
        # Get real balance
        if item.get('type') == 1:
            real_balance = item.get('amount')
        
        # Get tournament balance
        if item.get('type') == 2 and item.get('id') == tournament_user_id:
            tournament_balance = item.get('amount')
            tournament_id = item.get('tournament_id')
            tournament_name = item.get('tournament_name')
    
    # Check if tournament was found
    if tournament_id is None:
        return False, f"You are not registered for this tournament!"
    # Check if we have enough balance for rebuy
    if rebuy_amount and real_balance < rebuy_amount:
        return False, f"Insufficient balance. Need {rebuy_amount}, have {real_balance}"
    # Check if rebuy is allowed (tournament balance should be 0 or less than threshold)
    if tournament_balance > 100:
        return False, f"Cannot Rebuy. Balance is alrady {tournament_balance}"

    print(f"Performing rebuy for tournament: {tournament_name} (ID: {tournament_id})")
    return _rebuy(tournament_id)



tournamentId = int(input('enter tournament id: '))
iq.change_balancev2('TOURNAMENT', tournamentId)
_balance = iq.get_balance()
print('Tournament Balance: ', _balance)
if _balance <=0 :
    status, msg = tournament_rebuy(tournament_user_id=tournamentId)
    if status:
        time.sleep(1)
        print('Balance After REBUY: ', iq.get_balance())
    else:
        print(msg)