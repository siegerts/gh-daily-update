import os
from datetime import datetime
from pprint import pprint

import boto3
import requests

GH_API_TEAM_URL = "https://api.github.com/orgs/aws-amplify/teams/{}/members"

ENV = os.environ.get("ENV", None)
PROD = ENV == "dev"


if PROD:
    from aws_lambda_powertools import Logger
    logging = Logger(level="INFO", service="github-updates")

    REGION = os.environ["REGION"]
    TABLE_NAME = os.environ["STORAGE_DYNAMO41B205C8_NAME"]
    TOKEN = os.environ["TOKEN"]

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    repo_table = dynamodb.Table(TABLE_NAME)

    repos = repo_table.scan()['Items']


else:
    import logging
    from dotenv import load_dotenv

    logging.basicConfig(format='%(levelname)s:%(message)s',
                        level=logging.DEBUG)

    load_dotenv()
    REGION = os.environ.get("REGION")
    TABLE_NAME = os.environ.get("TABLE_NAME")
    TOKEN = os.environ.get("TOKEN")
    WEBHOOK = os.environ.get("WEBHOOK")

    repos = [
        {
            "id": "amplify-cli",
            "repo": "amplify-cli",
            "team": "amplify-cli",
            "name": "Amplify CLI"
        },
        {
            "id": "amplify-js",
            "repo": "amplify-js",
            "team": "amplify-js",
            "name": "Amplify JS"
        },
        {
            "id": "amplify-flutter",
            "repo": "amplify-flutter",
            "team": "amplify-flutter",
            "name": "Amplify Flutter"
        },
        {
            "id": "amplify-android",
            "repo": "amplify-android",
            "team": "amplify-native",
            "name": "Amplify Android"
        },
        {
            "id": "amplify-ios",
            "repo": "amplify-ios",
            "team": "amplify-native",
            "name": "Amplify iOS"
        },
        {
            "id": "amplify-codegen",
            "repo": "amplify-codegen",
            "team": "amplify-codegen",
            "name": "Amplify Codegen"
        },
        {
            "id": "amplify-adminui",
            "repo": "amplify-adminui",
            "team": "amplify-console",
            "name": "Amplify Admin UI"
        },
        {
            "id": "amplify-console",
            "repo": "amplify-console",
            "team": "amplify-console",
            "name": "Amplify Console"
        }
    ]


def get_team_members(id):
    # https://docs.github.com/en/rest/reference/teams#list-team-members
    headers = {"Accept": "application/vnd.github.v3+json",
               "Authorization": "token " + TOKEN
               }

    params = {"per_page": 100}

    req = requests.get(GH_API_TEAM_URL.format(id),
                       headers=headers, params=params)

    if req.status_code == requests.codes.ok:
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return {
            "members": [member["login"] for member in req.json()],
            "updated_members_at": now
        }


def put_repo_in_ddb(rec, dynamodb=None):
    if not dynamodb:
        dynamodb = boto3.resource('dynamodb', region_name=REGION)

    table = dynamodb.Table(TABLE_NAME)

    response = table.put_item(Item=rec)
    return response


def update_repo_members(repo_id, team_info, dynamodb=None):
    if not dynamodb:
        dynamodb = boto3.resource('dynamodb', region_name=REGION)

    table = dynamodb.Table(TABLE_NAME)

    response = table.update_item(
        Key={"id": repo_id},
        UpdateExpression="set members=:m, updated_members_at=:u",
        ExpressionAttributeValues={
            ':m': team_info["members"],
            ':u': team_info["updated_members_at"]
        },
        ReturnValues="UPDATED_NEW"
    )
    return response


def init_load_data():
    for repo in repos:
        members = get_team_members(repo["team"])
        webhook = {"webhook": WEBHOOK}
        repo_info = {**repo, **members, **webhook}
        resp = put_repo_in_ddb(repo_info)
        pprint(resp, sort_dicts=False)


def handler(event, context):
    logging.info("run mode: production")
    for repo in repos:
        logging.info(f"Updating members for {repo['id']}...")
        try:
            team_info = get_team_members(repo["team"])
            resp = update_repo_members(repo['id'], team_info)
        except Exception as err:
            logging.error(err)
        logging.info(resp)


# if __name__ == '__main__':

#     init_load_data()
