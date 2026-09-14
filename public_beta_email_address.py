"""Email syntax/IDNA normalization; verification proves ownership, not DNS guesses."""
from email_validator import validate_email, EmailNotValidError


def normalize_email(value, *, testing=False):
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError('请输入有效邮箱')
    try:
        result = validate_email(value.strip(), check_deliverability=False,
                                allow_smtputf8=False, test_environment=testing)
    except EmailNotValidError:
        raise ValueError('请输入有效邮箱') from None
    # Mio identities intentionally treat the entire address case-insensitively.
    return result.normalized, result.ascii_email.casefold()


def masked_email(value):
    if not value or '@' not in value:
        return None
    local, domain = value.rsplit('@', 1)
    return local[:1] + '***@' + domain
