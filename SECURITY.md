# Security Policy

## Welcome to pywrkr Security Reporting

We appreciate your help in keeping pywrkr secure. **Anyone in the community can report security vulnerabilities** — you don't need to be a project owner, maintainer, or contributor to submit a security report.

## Supported Versions

Use this section to tell people about which versions of your project are currently being supported with security updates.

| Version | Supported          |
| ------- | ------------------ |
| 5.1.x   | :white_check_mark: |
| 5.0.x   | :x:                |
| 4.8.x   | :white_check_mark: |
| < 4.0   | :x:                |

## Reporting a Vulnerability

### Who Can Report?

**Anyone can report a vulnerability**, regardless of whether they:
- Are part of the pywrkr development team
- - Have contributed code to the project
  - - Have an account on GitHub
    - - Have expertise in security
     
      - ### How to Report
     
      - We take all security reports seriously. To report a vulnerability:
     
      - 1. **Use GitHub's Private Vulnerability Reporting** (Recommended)
        2.    - Go to the [Security Advisories](../../security/advisories) page
              -    - Click "Draft a security advisory"
                   -    - Fill in the vulnerability details
                        -    - This keeps the report private until we publish a fix
                         
                             - 2. **Email Security Report** (Alternative)
                               3.    - Email your report to the maintainers with subject: "Security Vulnerability in pywrkr"
                                     -    - Include: vulnerability description, affected versions, steps to reproduce, and impact assessment
                                      
                                          - 3. **GitHub Discussions** (Non-Sensitive Issues)
                                            4.    - For general security questions (not specific vulnerabilities): use [Discussions](../../discussions)
                                              
                                                  - ### What to Include in Your Report
                                              
                                                  - Please provide as much detail as possible:
                                              
                                                  - - **Vulnerability Type**: SQL Injection, XSS, Authentication Bypass, etc.
                                                    - - **Affected Versions**: Which versions of pywrkr are vulnerable?
                                                      - - **Severity**: Critical, High, Medium, or Low
                                                        - - **Description**: Clear explanation of the vulnerability
                                                          - - **Steps to Reproduce**: How can we reproduce the issue?
                                                            - - **Proof of Concept**: Code example or test case (if possible)
                                                              - - **Impact**: What could an attacker do with this vulnerability?
                                                                - - **Suggested Fix**: Optional - any proposed solution
                                                                 
                                                                  - ### What to Expect
                                                                 
                                                                  - After you report a vulnerability:
                                                                 
                                                                  - 1. **Acknowledgment**: We'll confirm receipt within 48 hours
                                                                    2. 2. **Assessment**: We'll investigate and evaluate the severity (typically within 7 days)
                                                                       3. 3. **Updates**: We'll keep you informed about our progress
                                                                          4. 4. **Resolution**:
                                                                             5.    - We'll work on a fix
                                                                                   -    - For critical issues, we'll patch supported versions
                                                                                        -    - We'll publish a security advisory once a fix is released
                                                                                             - 5. **Credit**: We'll give you credit for the responsible disclosure (unless you prefer anonymity)
                                                                                              
                                                                                               6. ### Timeline
                                                                                              
                                                                                               7. - **Critical vulnerabilities**: Fixed within 24-48 hours of confirmation
                                                                                                  - - **High severity**: Fixed within 1-2 weeks
                                                                                                    - - **Medium severity**: Fixed in the next release cycle
                                                                                                      - - **Low severity**: Fixed as part of regular maintenance
                                                                                                       
                                                                                                        - ## Security Best Practices for Users
                                                                                                       
                                                                                                        - ### Using pywrkr Securely
                                                                                                       
                                                                                                        - 1. **Keep Updated**: Always use the latest stable version
                                                                                                          2. 2. **Monitor Releases**: Watch for security advisories in our releases
                                                                                                             3. 3. **Report Issues**: If you notice suspicious behavior, report it immediately
                                                                                                                4. 4. **Check Dependencies**: Review the security status of pywrkr's dependencies
                                                                                                                  
                                                                                                                   5. ### Responsible Disclosure
                                                                                                                  
                                                                                                                   6. If you discover a vulnerability, please help us by:
                                                                                                                   7. - **Reporting privately first** before disclosing publicly
                                                                                                                      - - **Giving us reasonable time** to patch before public disclosure
                                                                                                                        - - **Not accessing data** beyond what's needed to confirm the vulnerability
                                                                                                                          - - **Not disrupting service** for other users
                                                                                                                           
                                                                                                                            - ## Security Resources
                                                                                                                           
                                                                                                                            - - [GitHub Security Advisories](../../security/advisories)
                                                                                                                              - - [GitHub Private Vulnerability Reporting](../../security/policy)
                                                                                                                                - - [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
                                                                                                                                  - - [OWASP Top 10](https://owasp.org/www-project-top-ten/)
                                                                                                                                   
                                                                                                                                    - ## Questions?
                                                                                                                                   
                                                                                                                                    - If you have questions about this security policy or need clarification:
                                                                                                                                    - - Open a [Discussion](../../discussions) (public, non-sensitive questions)
                                                                                                                                      - - Check existing [Issues](../../issues) for similar questions
                                                                                                                                        - - Review [Documentation](../../wiki)
                                                                                                                                         
                                                                                                                                          - ---
                                                                                                                                          
                                                                                                                                          **Thank you for helping keep pywrkr secure!** We value the security research community and appreciate responsible disclosure practices.
