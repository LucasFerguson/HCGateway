"""Review account requests from the signup form.

Run inside the API container, which already has MONGO_URI:

    docker exec hcgateway_api python manage_accounts.py list
    docker exec hcgateway_api python manage_accounts.py approve <username>
    docker exec hcgateway_api python manage_accounts.py reject <username>

Approving moves the request into ``users`` with the password the person
chose when they asked, so they can sign in right away; nobody else ever
sees that password.
"""

import argparse
import os
import sys

import pymongo
from dotenv import load_dotenv

from account_requests import MAX_PENDING, approve_request, list_requests, reject_request
from credentials import normalize_username, printable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='show waiting requests')
    for name in ('approve', 'reject'):
        command = commands.add_parser(name, help=f'{name} a request')
        command.add_argument('username')
    args = parser.parse_args(argv)
    if args.command != 'list':
        # Requests are stored normalized, so match what the person typed.
        args.username = normalize_username(args.username) or args.username

    load_dotenv()
    mongo = pymongo.MongoClient(os.environ['MONGO_URI'])
    db = mongo['hcgateway']

    if args.command == 'list':
        requests = list_requests(db)
        if not requests:
            print('No account requests waiting.')
            return 0
        print(f'{len(requests)} waiting (the form stops queuing at {MAX_PENDING}):')
        for request in requests:
            requested = request['requestedAt'].strftime('%Y-%m-%d %H:%M UTC')
            username, client = printable(request['username']), printable(request.get('client') or 'unknown')
            print(f"  {username:<32} {requested}  via {client}")
        return 0

    if args.command == 'approve':
        if approve_request(db, args.username):
            print(f'Approved {args.username!r}; they can sign in now.')
            return 0
        print(f'No request for {args.username!r}, or that username already has an account.', file=sys.stderr)
        return 1

    if reject_request(db, args.username):
        print(f'Rejected and deleted the request for {args.username!r}.')
        return 0
    print(f'No request for {args.username!r}.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
