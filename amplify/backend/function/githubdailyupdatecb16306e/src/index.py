import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List

import boto3
import requests

ENV = os.environ.get("ENV", None)
PROD = ENV == "dev"

if PROD:
    from aws_lambda_powertools import Logger
    logging = Logger(level="INFO", service="github-updates")

    REGION = os.environ["REGION"]
    REPO_TABLE_NAME = os.environ["STORAGE_DYNAMO41B205C8_NAME"]
    TOKEN = os.environ.get("GH_TOKEN")

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    repo_table = dynamodb.Table(REPO_TABLE_NAME)

    repos = repo_table.scan()['Items']


else:
    import logging
    logging.basicConfig(format='%(levelname)s:%(message)s',
                        level=logging.DEBUG)

    from dotenv import load_dotenv
    load_dotenv()
    MEMBERS = json.loads(os.environ['MEMBERS'])
    WEBHOOK = os.environ.get("TEST_WEBHOOK")
    TOKEN = os.environ.get("TOKEN")

    # local mock
    repos = [{
        "id": "amplify-cli",
        "repo": "amplify-cli",
        "team": "amplify-cli",
        "webhook": WEBHOOK,
        "members": MEMBERS,
        "name": "Amplify CLI",
        "updated_members_at": "2021-05-20T22:25:52.409Z"
    }]


GITHUB_BASE_URL = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github.v3+json",
           "Authorization": "token " + TOKEN}


FILTER_LABELS = set(["feature-request", "enhancement",
                     "bug", "pending-close-response-required"])

TIME_INTERVAL = 4
TODAY = date.today()


def date_from_interval(interval: int) -> date:
    """Historical date based looking back week interval

    Args:
        interval (int): number of weeks to look back

    Returns:
        date: today less week interval(s)
    """

    today = date.today()
    return today - timedelta(weeks=interval)


def days_ago(count: int) -> str:
    """Humanize date count

    Args:
        count (int): number of days

    Returns:
        str: human-friendly representation
    """
    if count == 0:
        return "today"

    suffix = "day" if count == 1 else "days"
    return f"{count} {suffix} ago"


# def get_rate_limit(ENDPOINT="/rate_limit") -> List[dict]:
#     req = requests.get(GITHUB_BASE_URL + ENDPOINT, headers=HEADERS)
#     if req.status_code == requests.codes.ok:
#         return req.json()

def pr_is_approved(base_url: str, repo: str, pr_id: int, org: str = "aws-amplify") -> bool:
    """Determine if PR is awaiting approval or not

    Args:
        base_url (str): GH API URL
        repo (str): repository ID
        pr_id (int): ID
        org (str, optional): Defaults to "aws-amplify".

    Returns:
        bool: is PR approved
    """
    pr_url = f"{base_url}/repos/{org}/{repo}/pulls/{pr_id}/reviews"

    req = requests.get(pr_url, headers=HEADERS)

    is_approved = False
    for item in req.json():
        if item["state"] == "APPROVED":
            is_approved = True
            break

    return is_approved


def issue_repo(repository_url: str) -> str:
    return repository_url.split("/")[-1]


def pr_id(pr_link: str) -> str:
    return pr_link.split("/")[-1]


def get_issues(endpoint: str = "/search/issues") -> List[dict]:

    issues = []
    per_page = 100
    page = 1

    time_interval = date_from_interval(TIME_INTERVAL)
    created_at = f"created:>{time_interval}"

    filters = (" ").join([f"-label:{label}" for label in FILTER_LABELS])

    # TODO: only is:pr
    params = {"q": f"org:aws-amplify is:open {filters} {created_at}",
              "per_page": per_page, "page": page}

    req = requests.get(GITHUB_BASE_URL + endpoint,
                       headers=HEADERS, params=params)

    total_count = req.json()["total_count"]
    issues = req.json()["items"]

    logging.debug(f"total_count {total_count}, issue count: {len(issues)}")

    if total_count > per_page:
        logging.debug(f"issue count: {len(issues)}")

        cap = max(total_count, 900)
        while len(issues) < cap:

            params["page"] = params.get("page", 0) + 1
            req = requests.get(GITHUB_BASE_URL + endpoint,
                               headers=HEADERS, params=params)

            if req.json()["items"]:
                logging.debug('appending...')
                issues = issues + req.json()["items"]

                logging.debug(
                    f"issue_count: {len(issues)}, after page:{params.get('page')}")

            else:
                break

    return issues


def truncate_item(item: str, max_length: int) -> str:
    if len(item) > max_length:
        return item[:(max_length - 2)] + ".."
    return item


def get_issue_assignee(issue: Dict) -> str:
    """Determine the GH issue assignees

    Args:
        issue (Dict): a GitHub issue from the REST API 

    Returns:
        str: one, or more, assignees in comma sep str
    """

    assignees = issue.get("assignees", None)
    if assignees:
        return",".join([member["login"] for member in assignees])

    assignee = issue.get("assignee", None)
    if assignee:
        return assignee["login"]

    return "unassigned"


def days_since(issue_date: str) -> int:
    today = date.today()
    return (today - datetime.fromisoformat(issue_date[:-1]).date()).days


def get_issue_labels(issue: Dict) -> str:
    labels = issue.get("labels", None)
    if labels:
        return ", ".join([label["name"] for label in labels])

    return ""


def is_pr(issue: Dict) -> bool:
    """Determine if GH issue is PR

    Args:
        issue (Dict): GH issue

    Returns:
        bool: is PR or issue
    """

    if issue.get("pull_request", None):
        return True
    return False


def format_issue(issue: Dict) -> Dict:
    """Format GH to

    Args:
        issue (Dict): GH issue

    Returns:
        Dict: Consolidated issue 
    """
    pr = is_pr(issue)
    id = pr_id(issue["url"])
    repo = issue_repo(issue['repository_url'])
    is_approved = False

    if pr:
        is_approved = pr_is_approved(GITHUB_BASE_URL, repo, id)

    if pr and is_approved:
        return {}

    return {
        "repo": repo,
        "title": truncate_item(issue.get("title"), 50),
        "is_pr": pr,
        "is_approved": is_approved,
        "assignee": get_issue_assignee(issue),
        "comments": issue.get("comments", 0),
        "open_since": days_since(issue.get("created_at")),
        "last_updated": days_since(issue.get("updated_at")),
        "labels": get_issue_labels(issue),
        "link": issue["html_url"],
    }


def pr_alerts(issue: Dict) -> str:
    """Apply light business logic to GH PR

    Args:
        issue (Dict): GH issue

    Returns:
        str: Concatenated string of alert idicators
    """
    # within last 24hrs, follow up and action
    # FIRST_TIME_CONTRIBUTOR
    # PR has less than 2 comments
    # and last comment is from pr author
    time = ""
    action = ""
    unassigned = "👤" if issue["assignee"] == "unassigned" else ""
    comments = "🔴" if issue['comments'] < 2 else ""

    if issue['last_updated'] > 2 or issue['open_since'] < 1:
        time = "⏰"

    if issue['last_updated'] < 2 and issue['open_since'] > 7:
        action = "🤔"

    return f"{time}{unassigned}{comments}{action}"


def issue_alerts(issue: Dict) -> str:
    """Apply light business logic to GH issue

    Args:
        issue (Dict): GH issue

    Returns:
        str: Concatenated string of alert idicators
    """
    action = ""
    unassigned = "👤" if issue["assignee"] == "unassigned" else ""
    comments = "🔴" if issue['comments'] == 0 else ""
    time = "⏰" if issue['last_updated'] > 2 else ""

    if issue['last_updated'] < 2 and issue['open_since'] > 7:
        action = "🤔"

    return f"{time}{unassigned}{comments}{action}"


def format_by_repo(issues: List[Dict]) -> Dict[str, str]:
    MSG_MAX_LENGTH = 40000

    sorted_issues_by_assignee = sorted(
        issues, key=lambda k: (k['assignee'], k['is_pr']))

    # issues_txt = ""
    prs_txt = ""

    for issue in sorted_issues_by_assignee:
        status_length = len(prs_txt)
        # status_length = len(prs_txt + issues_txt)
        labels = f"({issue['labels']})" if issue['labels'] else ""

        if issue["is_pr"]:
            pr = f"---\n{pr_alerts(issue)} [{issue['assignee']}] {issue['title']}\n{issue['comments']} comments, created: {days_ago(issue['open_since'])}, updated: {days_ago(issue['last_updated'])} {labels}\n{issue['link']}\n\n"
            if (len(pr) + status_length) > MSG_MAX_LENGTH:
                # indicate truncate
                break
            else:
                prs_txt += pr
        # else:
        #     issue_rec = f"---\n{issue_alerts(issue)} [{issue['assignee']}] {issue['title']}\n{issue['comments']} comments, created: {days_ago(issue['open_since'])}, updated: {days_ago(issue['last_updated'])} {labels}\n{issue['link']}\n\n"
        #     if (len(issue_rec) + status_length) > MSG_MAX_LENGTH:
        #         # indicate truncate
        #         break
        #     else:
        #         issues_txt += issue_rec

    return {
        "prs": prs_txt
        # "issues": issues_txt
    }


def create_status_reports() -> None:
    repo_ids = [repo["repo"] for repo in repos]

    repo_count = len(repo_ids)
    if repo_count > 10:
        logging.warning(
            "10 requests per minute for searching. Repo count: {repo_count}")

    try:
        issues = get_issues()
    except Exception as err:
        logging.error("Failed to fetch issues. Exiting...")
        logging.error(err)
        sys.exit()

    members_by_repo = {repo["repo"]: repo["members"] for repo in repos}
    webhook_by_repo = {repo["repo"]: repo["webhook"] for repo in repos}
    name_by_repo = {repo["repo"]: repo["name"] for repo in repos}

    filtered_issues = {repo: [] for repo in repo_ids}

    # one pass to clean up
    for issue in issues:
        repo = issue_repo(issue["repository_url"])
        author = issue["user"]["login"]

        if repo in repo_ids and \
                author not in members_by_repo[repo]:

            formatted_issue = format_issue(issue)
            if formatted_issue:
                filtered_issues[repo].append(formatted_issue)

    status = []
    meta = {
        "interval": str(TIME_INTERVAL),
        "filters": (", ").join(list(FILTER_LABELS)),
        "date": str(TODAY),
    }

    for repo_id in repo_ids:
        issues = filtered_issues[repo_id]
        if issues:
            status.append({
                **{"repo": name_by_repo[repo_id]},
                **{"webhook": webhook_by_repo[repo_id]},
                **meta,
                **format_by_repo(issues)
            })

    logging.info(f"{len(status)} reports to send.")

    for item in status:
        try:
            logging.info(f"Sending webhook for {item['repo']}")
            req = requests.post(item["webhook"], json=item)
            logging.info(req)
        except Exception as err:
            logging.error("Failed to post webhook. Skipping...")
            logging.error(err)
            continue


def handler(event, context):
    logging.info(f"run mode: production")
    create_status_reports()


if __name__ == "__main__":
    create_status_reports()
