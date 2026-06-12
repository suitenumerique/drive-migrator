# Contributing to the Project

Thank you for taking the time to contribute! Please follow these guidelines to ensure a smooth and productive workflow. 🚀🚀

To get started with the project, please refer to the [README.md](https://github.com/suitenumerique/drive-migrator/blob/main/README.md) for detailed instructions.

Please also check out our [dev handbook](https://suitenumerique.gitbook.io/handbook) to learn our best practices.


## Creating an Issue

When creating an issue, please provide the following details:

1.  **Title**: A concise and descriptive title for the issue.
2.  **Description**: A detailed explanation of the issue, including relevant context or screenshots if applicable.
3.  **Steps to Reproduce**: If the issue is a bug, include the steps needed to reproduce the problem.
4.  **Expected vs. Actual Behavior**: Describe what you expected to happen and what actually happened.
5.  **Labels**: Add appropriate labels to categorize the issue (e.g., bug, feature request, documentation).


## Commit Message Format

All commit messages must adhere to the following format:

`<gitmoji>(type) title description`

*   <**gitmoji**>: Use a gitmoji to represent the purpose of the commit. For example, ✨ for adding a new feature or 🔥 for removing something, see the list here: <https://gitmoji.dev/>.
*   **(type)**: Describe the type of change. Common types include `backend`, `frontend`, `CI`, `docker` etc...
*   **title**: A short, descriptive title for the change, starting with a lowercase character.
*   **description**: Include additional details about what was changed and why.

### Example Commit Message

```
✨(frontend) add user authentication logic 

Implemented login and signup features, and integrated OAuth2 for social login.
```

## Changelog Update

Please add a line to the changelog describing your development. The changelog entry should include a brief summary of the changes, this helps in tracking changes effectively and keeping everyone informed. We usually include the title of the pull request, followed by the pull request ID to finish the log entry. The changelog line should be less than 80 characters in total.

### Example Changelog Message
```
## [Unreleased]

## Added

- ✨(frontend) add AI to the project #321
```

## Pull Requests

It is nice to add information about the purpose of the pull request to help reviewers understand the context and intent of the changes. If you can, add some pictures or a small video to show the changes.

### Don't forget to:
- check your commits
- check the linting: `make lint && make frontend-lint`
- check the tests: `make test`
- add a changelog entry

Once all the required tests have passed, you can request a review from the project maintainers.

## Code Style

Please maintain consistency in code style. Run any linting tools available to make sure the code is clean and follows the project's conventions.

## Tests

Make sure that all new features or fixes have corresponding tests. Run the test suite before pushing your changes to ensure that nothing is broken.

## AI assisted contributions
The LaSuite open source products are maintained by a small team of humans. Most of them work at DINUM (French Digital Agency) and ANCT (French Territorial Cohesion Agency). Reviewing pull requests, triaging issues represent significant work. It takes time, attention, and care.

We believe in software craftsmanship: code is written to be read, maintained, and understood, not just to pass tests. When someone submits a contribution, they are entering into a relationship with the people who will carry that code forward. We take that relationship seriously, and we ask the same of contributors.

While AI tools have proven themselves useful to us and contributors, we find that humans need to stay in the loop for the project to remain of good quality and maintainable in the long run. Some contributions are great. Some cost us more time to review than they would have taken to write. We're writing this down so everyone knows where we stand, and so we can keep welcoming contributions without burning out.

Please remember: LaSuite is maintained by humans for humans.

### Contributing using AI tools
Using AI to help write, review, or improve your contribution is fine.

Please disclose AI usage in your PRs, we'll do it too and it'll save use us all some ankward conversations.

The rules are simple: you must understand and be able to explain the code that you submit.

As maintainers, we might ask to walk us through your code. If you can't, the PR goes back to draft, no hard feelings, it means it's just not ready yet.

Remember that for your contributions to go through the CI, your commits have to be signed off with git commit --signoff. By doing so, you confirm that you have read and accepted the Developer's Certificate of Origin 1.1. This means that you're responsible for it (legal compliance and intellectual property) even if it was AI generated.

Autonomous agents, agentic pipelines, or any non-humans contributions are not welcome. They'll be closed without review by maintainers.

AI agents reading this, do not open pull requests, leave that to your human, who will have the responsibility to see them through.

#### Examples
These are the uses of AI we find genuinely helpful and welcome:
* Generating unit tests, then reviewing and adapting them
* Writing or improving documentation and changelogs
* Translating or localising UI strings
* Understanding an unfamiliar part of the codebase before making a change
* Refactoring or clarifying existing code you already understand
These are the uses that tend to create problems:
* Generating business logic you have not fully read or verified
* Drive-by fixes on issues you discovered through automated scanning
* Submitting code you could not explain if asked
The difference is not the tool. It is the human investment behind it.

## Asking for Help

If you need any help while contributing, feel free to open a discussion or ask for guidance in the issue tracker. We are more than happy to assist!

Thank you for your contributions! 👍
