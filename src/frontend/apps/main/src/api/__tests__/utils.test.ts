import { errorCauses } from '../utils';

const jsonResponse = (status: number, body: unknown): Response =>
  ({
    status,
    json: () => Promise.resolve(body),
  }) as Response;

describe('errorCauses', () => {
  it('flattens string and list field errors and keeps the status', async () => {
    const response = jsonResponse(400, {
      name: ['required', 'too short'],
      code: 'invalid',
    });

    await expect(errorCauses(response, { id: 1 })).resolves.toEqual({
      status: 400,
      cause: ['required', 'too short', 'invalid'],
      data: { id: 1 },
    });
  });

  it('returns no cause when the body is null', async () => {
    const response = jsonResponse(500, null);

    await expect(errorCauses(response)).resolves.toEqual({
      status: 500,
      cause: undefined,
      data: undefined,
    });
  });

  it('returns an empty cause list when the body has no fields', async () => {
    const response = jsonResponse(422, {});

    await expect(errorCauses(response)).resolves.toEqual({
      status: 422,
      cause: [],
      data: undefined,
    });
  });
});
