"""Status / role values stored in the database.

Plain class attributes (not Enum) so the values stay ordinary str everywhere:
in SQL parameters, JSON responses and Jinja output. The stored values never change.
"""


class OrderStatus:
    INQUIRY = 'inquiry'          # chat-only placeholder order
    PENDING = 'pending'          # waiting for the trainer to accept
    CONFIRMED = 'confirmed'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'


class PaymentStatus:
    UNPAID = 'unpaid'
    PAID = 'paid'
    REFUNDED = 'refunded'


class Role:
    MEMBER = 'member'
    TRAINER = 'trainer'
    ADMIN = 'admin'


class ReviewType:
    BUYER_TO_TRAINER = 'buyer_to_trainer'
    TRAINER_TO_BUYER = 'trainer_to_buyer'


class TrainerLevel:
    GENERAL = 'general'
    ADVANCED = 'advanced'
    PROFESSIONAL = 'professional'
