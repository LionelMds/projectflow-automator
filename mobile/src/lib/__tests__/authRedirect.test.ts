import { isRedirect, REDIRECT_URI } from '@/lib/authRedirect';

describe('isRedirect', () => {
  it('catches the reply to the desktop redirect, with or without port and query', () => {
    expect(isRedirect(REDIRECT_URI)).toBe(true);
    expect(isRedirect('http://localhost/?code=abc&state=xyz')).toBe(true);
    expect(isRedirect('http://localhost?code=abc')).toBe(true);
    expect(isRedirect('http://localhost:53682/?error=access_denied')).toBe(true);
    expect(isRedirect('HTTP://LOCALHOST/?code=abc')).toBe(true);
  });

  it('lets the Microsoft pages and look-alike hosts load', () => {
    expect(
      isRedirect(
        'https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize?redirect_uri=http%3A%2F%2Flocalhost&client_id=x',
      ),
    ).toBe(false);
    expect(isRedirect('http://localhost.example.com/?code=abc')).toBe(false);
    expect(isRedirect('http://localhostevil.com/')).toBe(false);
    expect(isRedirect('https://localhost/?code=abc')).toBe(false);
  });
});
