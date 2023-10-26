import uvicorn
import datetime
import os
from datetime import timedelta
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi_sqlalchemy import DBSessionMiddleware, db
from sqlalchemy import or_
from typing import Union
from sgor_core.schemas.admin_schema import CreateUser as SchemaCreateUser,ListUser as SchemaListUser, ListSportsGear as SchemaListSportsGear, \
UpdateUser as SchemaUpdateUser, RentSportsGear as SchemaRentSportsGear

from sgor_core.schemas.auth_schema import Token
from sgor_core.models import SportsGear, User, UserRental

from sgor_core.utils import check_if_user_exists, find_days_between_dates
from sgor_core.auth import get_password_hash, authenticate_user, ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token, get_current_active_user
from typing import List, Annotated

from fastapi.security import OAuth2PasswordRequestForm


app = FastAPI(title="Sports Gear Renting User")

# to avoid csrftokenError
app.add_middleware(DBSessionMiddleware, db_url=os.environ['DATABASE_URL'])


@app.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends()
):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

  
@app.post('/user/create', response_model=SchemaListUser)
async def create_user(user:SchemaCreateUser):
    if check_if_user_exists(user.email):
        raise HTTPException(status_code=400, detail="User with this email already exist")
    pwd_hash = get_password_hash(user.password)
    db_user = User(name=user.name, email=user.email, phone_number=user.phone_number, address=user.address, password=pwd_hash)
    db.session.add(db_user)
    db.session.commit()
    return db_user


@app.get('/user/view', response_model=SchemaListUser)
async def user_view(
    current_user: User = Depends(get_current_active_user)
):
    return current_user


@app.patch('/user/update', response_model=SchemaListUser)
async def update_user(
    user_update: SchemaUpdateUser, current_user: User = Depends(get_current_active_user)
):
    user_data = user_update.dict(exclude_unset=True)
    for key, value in user_data.items():
        if value:
            setattr(current_user, key, value)
    db.session.add(current_user)
    db.session.commit()
    db.session.refresh(current_user)
    return current_user


@app.get('/sportsgears', response_model=List[SchemaListSportsGear])
async def sports_gears(
    query: Union[str, None] = None, current_user: User = Depends(get_current_active_user)
):
    sports_gears = db.session.query(SportsGear)
    if query:
        sports_gears = sports_gears.filter(
            or_(
                SportsGear.name.ilike('%{}%'.format(query)), 
                SportsGear.sport.ilike('%{}%'.format(query))
            ))
    return sports_gears.all()


@app.post('/user/sportsgear/{sports_gear_id}/rent')
async def sports_gear_rent(
    sports_gear_id: int, rent_sports_gear: SchemaRentSportsGear, current_user: User = Depends(get_current_active_user)
):
    sports_gear = db.session.query(SportsGear).get(sports_gear_id)

    if not sports_gear:
        raise HTTPException(status_code=404, detail="Sports gear not found")
    
    if sports_gear.available_count < rent_sports_gear.item_count:
        if sports_gear.available_count == 0:
            raise HTTPException(status_code=409, detail="Unable to process your request. This item is sold out") 
        raise HTTPException(status_code=409, detail="Unable to process your request. Only {} no of item you requested is available".format(sports_gear.available_count)) 

    user_rental = UserRental(user_id=current_user.id, sports_gear_id=sports_gear_id, rented_sports_gear_count=rent_sports_gear.item_count, user_requested_duration_in_days=rent_sports_gear.rental_duration)
    db.session.add(user_rental)

    sports_gear.available_count = sports_gear.available_count - rent_sports_gear.item_count
    db.session.add(sports_gear)
    
    db.session.commit()
    db.session.refresh(sports_gear)
    return user_rental.serialize()

@app.get('/user/sportsgear/rentals/view', )
async def user_rentals_view(
    current_user: User = Depends(get_current_active_user)
):
    past_rentals = db.session.query(UserRental).filter(UserRental.user_id == current_user.id, UserRental.rental_end_date.isnot(None)).order_by(UserRental.rental_started.desc())
    current_rentals = db.session.query(UserRental).filter(UserRental.user_id == current_user.id, UserRental.rental_end_date.is_(None)).order_by(UserRental.rental_started.desc())

    return {
        'past_rentals': [rental.serialize() for rental in past_rentals],
        'current_rentals': [rental.serialize() for rental in current_rentals]
    }

@app.get('/test')
async def test():
    return {'a': datetime.datetime.now(), 'b': datetime.datetime.utcnow()}


@app.post('/user/sportsgear/{user_rental_id}/return')
async def sports_gear_rturn(
    user_rental_id: int, current_user: User = Depends(get_current_active_user)
):
    user_rental = db.session.query(UserRental).filter(UserRental.id == user_rental_id, UserRental.user_id == current_user.id).first()
    if not user_rental:
        raise HTTPException(status_code=404, detail="User Rental not found")

    if user_rental.rental_end_date:
        raise HTTPException(status_code=400, detail="This item is already returned")
    
    total_rent_days = find_days_between_dates(user_rental.rental_started, datetime.datetime.now(datetime.timezone.utc))
    sports_gear = user_rental.sports_gear
    rent_per_day = user_rental.sports_gear.rent_per_day
    total_rent = total_rent_days * rent_per_day * user_rental.rented_sports_gear_count

    sports_gear.available_count = sports_gear.available_count + user_rental.rented_sports_gear_count

    user_rental.total_rent = total_rent

    db.session.add(user_rental)
    db.session.add(sports_gear)
    db.session.commit()
    db.session.refresh(user_rental)
    return user_rental.serialize()
