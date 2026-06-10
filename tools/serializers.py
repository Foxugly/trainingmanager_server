from rest_framework import serializers


class TokensUsedSerializer(serializers.Serializer):
    """The ``{input, output}`` token-usage block returned by every AI endpoint
    (training/plan generation, session explain, training-block review). Shared
    so the shape is declared once instead of an inline_serializer per endpoint.
    """

    input = serializers.IntegerField()
    output = serializers.IntegerField()
