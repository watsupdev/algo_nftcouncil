from pyteal import *
from pyteal.ast.bytes import Bytes
from pyteal_helpers import program

version = "2.1.0"  

# Release notes:
# upgraded from 2.0 to 2.1 because the contract methods have changed to conserve space

"""
Contract life cycle:
1. create, mint degen2
2. init funding
3. degen opt in
3.5 users local state opt in (want users to do this before they swap)
4. week 1 users can swap degen for degen2, remainder stays in contract (use swap ratio to give only 10% of tokens)
5. use diff types of proposals which require a vote to pass: withdraw, change manager, 
6. managers can kill a proposal at any time (necessary if someone finds a flaw in the proposal system)
7. Need a 1 day grace period between votes! So that managers and people have a minute to carryout whatever is needed

Maybe the manager can do freeze and clawback, but I don't think it makes sense to do the freeze and clawback by vote.
it gives the bad actor too much time to do bad things. maybe just clawback

right now, the contract has an issue where it may not have enough tokens to pay back stakers 
I need to reserve all staked tokens, meaning that every time someone stakes I need to keep track of total stake
tokens cannot leave the contract if the balance is less than total stake no buy swap or propose
"""

def approval():
    # globals 
    degen2 = Bytes("d2")
    swap_token1 = Bytes("st")
    swap_ratio1 = Bytes("sr")
    total_stake = Bytes("tl")  # uint64, must keep track of total stake otherwise people can buy/swap more than is staked

    upvotes = Bytes("uv")  # uint64, people want to see how many were in favor and against, store these explicitly
    dnvotes = Bytes("dv")  # uint64
    end_time = Bytes("et")  # uint64, when vote expires in unix
    duration = Bytes("r")  # uint64, increment for end time
    cooldown = Bytes("o")  # uint64, grace period
    threshold = Bytes("t")  # uint64, increment for end time
    price = Bytes("pc")  # uint64, increment for end time
    proposal_fee = Bytes("pf")  # uint64, increment for end time
    end_creator_opt_in = Bytes("e")  # uint64, immutable
    end_swap1 = Bytes("es")  # uint64, mutable by vote
    
    proposal = Bytes("p")  # bytes
    proposal_type = Bytes("pt")  # bytes
    proposal_index = Bytes("pk")  # uint64, an ASA ID or App ID referenced by the proposal (flexible)
    proposal_value = Bytes("pv")  # uint64, how much the receiver will get if the proposal passes
    receiver_address = Bytes("ra")  # bytes, this one is partially mutable, only after the end time

    # locals
    stake = Bytes("s")  # uint64
    last_vote = Bytes("lv")  # uint64
 
    # ops
    op_swap1 = Bytes("sw") 
    op_buy = Bytes("b") 
    op_upvote = Bytes("u") 
    op_dnvote = Bytes("d")
    op_create_token = Bytes("ct")
    op_token_opt_in = Bytes("oi")
    op_token_opt_out = Bytes("oo")
    op_propose = Bytes("pr")
    op_local_stake = Bytes("ls")
    op_execute = Bytes("x")
    op_withdraw = Bytes("w")
    op_creator_token_opt_in = Bytes("ci")
    op_slash_stake = Bytes("ss")
    op_clawback = Bytes("cb")
    op_change_pf = Bytes("cp")
    op_change_duration = Bytes("cd")
    op_change_threshold = Bytes("ch")
    op_change_price = Bytes("cr")
    op_pay_algo = Bytes("pa")
    op_pay_token = Bytes("n")
    op_start_swap1 = Bytes("sp")
    op_upgrade = Bytes("a")

    # utils
    empty = Bytes("A")
    min_duration = Int(600)  # CHANGE to 600, only used for change_duration (need a fast minimum in case mass NFT withdraw)
    week = Int(3600*24*7)  # only used for end_swap CHANGE 3600*24*7
    has_stake = App.localGet(Txn.sender(), stake) > Int(0)
    is_proposal_over = And(
        App.globalGet(proposal) != empty,  # might not be necessary... must be active proposal, prevents users from repeatedly calling it
        App.globalGet(end_time) < Global.latest_timestamp(), # after vote
        App.globalGet(end_time) + App.globalGet(cooldown) > Global.latest_timestamp(),  # within grace period
    )
    did_proposal_pass = And(
        App.globalGet(upvotes)>App.globalGet(dnvotes), # more up than down
        (App.globalGet(upvotes)+App.globalGet(dnvotes))>(App.globalGet(threshold)),  # above threshold
        # for percent threshold: (App.globalGet(upvotes)+App.globalGet(dnvotes))>(App.globalGet(threshold)*Int(420000069)/Int(1000)),  # above threshold
        
    )
    will_proposal_give_degen2 = And(  # proposal must be active/possibly executable!
        App.globalGet(proposal) != empty, 
        App.globalGet(proposal_type) == op_pay_token, 
        App.globalGet(proposal_index) == App.globalGet(degen2),
        App.globalGet(proposal_value) > Int(0),
        App.globalGet(end_time) + App.globalGet(cooldown) > Global.latest_timestamp()  # within grace period (need to check if it is active or can be executed)
    )
    reset = Seq(
        [
            App.globalPut(upvotes, Int(0)),  # votes to 0
            App.globalPut(dnvotes, Int(0)),  # votes to 0
            App.globalPut(proposal, empty),  # proposal to empty
            App.globalPut(proposal_type, empty),  # proposal to empty
            App.globalPut(proposal_index, Int(0)),  # default is algo

            # need to enforce a cooldown period, can't do this. Failed proposals must 
            #App.globalPut(end_time, Global.latest_timestamp() - App.globalGet(cooldown)),  # makes sure we are past the grace period 
            App.globalPut(receiver_address, Global.zero_address())  # remove reciever address so people can withdraw
        ]
    )
    
    # app calls
    # phase 1, initialization
    on_creation = Seq(  # no risk
        [   
            Assert(Btoi(Txn.application_args[0]) >= App.globalGet(cooldown)),  # make sure duration isn't too short
            Assert(Btoi(Txn.application_args[1]) >= Int(1)),  # proposal fee has to be > 1 degen2
            App.globalPut(upvotes, Int(0)),  # initialize the vote at 0
            App.globalPut(dnvotes, Int(0)),  # initialize the vote at 0
            App.globalPut(duration, Btoi(Txn.application_args[0])),  # initialize the duration
            App.globalPut(cooldown, Int(3600*24)),  # initialize the cooldown at 1 day, with 3 day duration
            App.globalPut(proposal_fee, Btoi(Txn.application_args[1])),  # initialize the fee to make a new proposal
            App.globalPut(proposal, empty),  # init proposal as empty
            App.globalPut(proposal_type, empty),  # init proposal as empty
            App.globalPut(proposal_index, Int(0)),  # init proposal token as 0 by default
            App.globalPut(threshold, Btoi(Txn.application_args[2])),  # minimum amount of votes before a proposal can be passed
            App.globalPut(swap_token1, Btoi(Txn.application_args[3])),  # Specify token! Left it like this so I can specify mainnet or testnet tokens rather than hardcoding
            App.globalPut(degen2, Int(0)),  # init as 0 so we only change once
            App.globalPut(price, Int(10000)),  # init price as 1 degen/10000 microalgo
            App.globalPut(total_stake, Int(0)),  # init price as 1 degen/10000 microalgo
            App.globalPut(receiver_address, Global.zero_address()),
            App.globalPut(end_creator_opt_in, Global.latest_timestamp()+week),  # immutable, 1 day to opt in
            App.globalPut(end_swap1, Global.latest_timestamp()+week),  # only allow free swaps for the first week, excess degen2 can be purchased from the contract or 
            App.globalPut(swap_ratio1, Int(1)),  # Specify swap "price"

            Approve(),
        ]
    )

    create_token = Seq(
        [
            Assert(Txn.sender() == Global.creator_address()),
            Assert(App.globalGet(degen2) == Int(0)),  # only create the token once! otherwise you can permanently fuck everyone

            # create the token 
            InnerTxnBuilder.Begin(),    
            InnerTxnBuilder.SetFields({ 
                TxnField.type_enum: TxnType.AssetConfig,
                TxnField.config_asset_name: Bytes("DEGEN DAO "+version),
                TxnField.config_asset_unit_name: Bytes("DEGEN2"),
                TxnField.config_asset_manager: Global.current_application_address(),
                TxnField.config_asset_clawback: Global.current_application_address(),
                TxnField.config_asset_reserve: Global.current_application_address(),
                TxnField.config_asset_total: Int(420000069),
                TxnField.config_asset_decimals: Int(0),
            }),
            # Submit the transaction we just built
            InnerTxnBuilder.Submit(),   
            App.globalPut(degen2, InnerTxn.created_asset_id()),
            Approve()
        ]
    )

    creator_token_opt_in = Seq(  # only the creator can opt in to new tokens on day 1
        [
            Assert(Txn.sender() == Global.creator_address()),  
            Assert(Global.latest_timestamp() < App.globalGet(end_creator_opt_in)),  # only for the first day
            Assert(Global.group_size() >= Int(1)),

            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.asset_receiver: Global.current_application_address(),
                TxnField.asset_amount: Int(0),
                TxnField.xfer_asset: Txn.assets[0], # Must be in the assets array sent as part of the application call
            }),
            InnerTxnBuilder.Submit(),
            Approve(),
        ]
    )

    contract_opt_in = Seq(  # allow anyone to opt in, create some local state variables
        [   
            App.localPut(Txn.sender(), stake, Int(0)),  # initialize local stake
            App.localPut(Txn.sender(), last_vote, Int(0)),  # initialize local time of last vote
            Approve(),
        ]
    )

    buy = Seq(  # public
        [   
            # scratch vars
            asset_balance := AssetHolding.balance(Global.current_application_address(), Txn.assets[0]),

            # Safety Checks
            Assert(Txn.assets[0] == App.globalGet(degen2)),
            #Assert(Global.latest_timestamp()<App.globalGet(end_swap)),  # check that we're in the first week
            Assert(Gtxn[Global.group_size()-Int(1)].type_enum() == TxnType.ApplicationCall),
            Assert(Gtxn[Global.group_size()-Int(1)].application_args.length() == Int(1)),  # need one for the noop "buy"
            # make sure they're sending assets only to the receiver addr
            # accept either algo TODO: or galgo (ran out of space!) :(
            Cond(  # TODO need to add conditional logic if there is a proposal and proposal_index == degen2, people can't buy if the proposal amount takes the contract balance under the total stake
                [  # TODO: I used the cond because I wanted galgo, now it's unnecessary, remove it!
                    # it needs at least 1 argument so we're fine to leave it in technically 
                    And(
                        (Gtxn[Global.group_size()-Int(2)].type_enum() == TxnType.Payment),
                        (Gtxn[Global.group_size()-Int(2)].receiver() ==  Global.current_application_address()),  # give algo to contract
                        (Gtxn[Global.group_size()-Int(2)].amount() >= App.globalGet(price)), # microalgos, make sure they are purchasing greater than the minimum qt.
                        # check if we have enough to cover the stake, depends on whether there is an active proposal or not
                        If(
                            will_proposal_give_degen2,
                            (App.globalGet(total_stake) <= asset_balance.value() - App.globalGet(proposal_value) - Gtxn[Global.group_size()-Int(2)].amount()/App.globalGet(price)),  # total stake >= asset balance - request - buy
                            (App.globalGet(total_stake) <= asset_balance.value() - Gtxn[Global.group_size()-Int(2)].amount()/App.globalGet(price)),  # total stake >= asset balance - buy
                        )
                    ),
                    Seq([
                        InnerTxnBuilder.Begin(),
                        InnerTxnBuilder.SetFields({
                            TxnField.type_enum: TxnType.AssetTransfer,
                            TxnField.asset_receiver: Gtxn[Global.group_size()-Int(2)].sender(),  # send to the addr that paid the algo
                            TxnField.asset_amount: Gtxn[Global.group_size()-Int(2)].amount()/App.globalGet(price),  # automatically applies floor function
                            TxnField.xfer_asset: Txn.assets[0], # Must be in the assets array sent as part of the application call
                        }),
                        InnerTxnBuilder.Submit(),
                    ])
                ]
            ),
            Approve(),
        ]
    )

    swap1 = Seq(  # public
        [   
            # scratch vars
            asset_balance := AssetHolding.balance(Global.current_application_address(), Txn.assets[0]),

            # Safety Checks
            Assert(Txn.assets[0] == App.globalGet(degen2)),
            Assert(Global.latest_timestamp()<App.globalGet(end_swap1)),  # check that we're in the swap period
            Assert(Gtxn[Global.group_size()-Int(1)].type_enum() == TxnType.ApplicationCall),
            Assert(Gtxn[Global.group_size()-Int(1)].application_args.length() == Int(1)),  # need one for the noop "buy"
            # make sure they're sending assets only to the receiver addr
            Assert(Gtxn[Global.group_size()-Int(2)].type_enum() == TxnType.AssetTransfer),
            Assert(Gtxn[Global.group_size()-Int(2)].xfer_asset() == App.globalGet(swap_token1)),
            Assert(Gtxn[Global.group_size()-Int(2)].asset_receiver() ==  Global.current_application_address()),  # give algo to contract
            Assert(Gtxn[Global.group_size()-Int(2)].asset_amount() >= App.globalGet(swap_ratio1)), # atomic units, make sure they are purchasing greater than the minimum qt.
            
            # check if we have enough to cover the stake, depends on whether there is an active proposal or not
            If(
                will_proposal_give_degen2,
                Assert(App.globalGet(total_stake) <= asset_balance.value() - Gtxn[Global.group_size()-Int(2)].asset_amount()/App.globalGet(swap_ratio1) - App.globalGet(proposal_value)),  # total stake <= asset balance - request - buy
                Assert(App.globalGet(total_stake) <= asset_balance.value() - Gtxn[Global.group_size()-Int(2)].asset_amount()/App.globalGet(swap_ratio1)),  # total stake <= asset balance - request
            ),

            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.asset_receiver: Gtxn[Global.group_size()-Int(2)].sender(),  # send to the addr that paid the algo
                TxnField.asset_amount: Gtxn[Global.group_size()-Int(2)].asset_amount()/App.globalGet(swap_ratio1),  # automatically applies floor function
                TxnField.xfer_asset: Txn.assets[0], # Must be in the assets array sent as part of the application call
            }),
            InnerTxnBuilder.Submit(),

            Approve(),
        ]
    )

    local_stake = Seq(
        [   
            Assert(Gtxn[0].type_enum() == TxnType.AssetTransfer),
            Assert(Gtxn[0].xfer_asset() ==  App.globalGet(degen2)),  # make sure they're using the right token
            Assert(Gtxn[0].asset_receiver() ==  Global.current_application_address()),  # deposit into contract

            # update stake by replacing stake with stake + asset_amount
            App.localPut(Txn.sender(), stake, App.localGet(Txn.sender(), stake) + Gtxn[0].asset_amount()),

            # add to total stake
            App.globalPut(total_stake, App.globalGet(total_stake) + Gtxn[0].asset_amount()),
            Approve()
        ]
    )

    # phase 2: proposal cycling
    # create proposal
    propose = Seq(
        # ?TODO: you could add a minimum proposal threshold, right now anyone can propose as long as they pay the fee 
        [  # 
            # scratch vars
            asset_balance := AssetHolding.balance(Global.current_application_address(), Txn.assets[0]),

            # Safety Checks
            Assert(Global.group_size() == Int(3)),
            Assert(Gtxn[1].type_enum() == TxnType.Payment),  
            Assert(Gtxn[1].amount() >= Int(2000000)),  # pay 2 algo in, 1 for contract, 1 for executor
            Assert(Gtxn[1].receiver() == Global.current_application_address()), 

            Assert(Gtxn[2].type_enum() == TxnType.AssetTransfer),  
            Assert(Gtxn[2].asset_amount() >= App.globalGet(proposal_fee)),  # pay proposal fee in degen2
            Assert(Gtxn[2].xfer_asset() >= App.globalGet(degen2)),  # pay proposal fee in degen2
            Assert(Gtxn[2].asset_receiver() == Global.current_application_address()), 

            # only really needto check that we're past the grace_period
            # Assert(App.globalGet(proposal) == empty),  # must be empty
            Assert(App.globalGet(end_time) + App.globalGet(cooldown) < Global.latest_timestamp()),  # grace period has passed
            If(
                Txn.application_args[2]==op_pay_algo,  # can only propose < 10% algo treasury, can fully withdraw all other tokens though (except degen2)
                If(  # if proposal is "upgrade", make sure you request <= balance-minbalance, if not then make sure only 10% balance
                    Txn.application_args[1]==op_upgrade,
                    Assert(Btoi(Txn.application_args[3]) <= Balance(Global.current_application_address())-MinBalance(Global.current_application_address())),
                    Assert(Btoi(Txn.application_args[3]) < Int(1)*Balance(Global.current_application_address())/Int(10)),  
                )
            ),
            If(
                And(
                    Txn.application_args[2]==op_pay_token,  # can only withdraw 20 million degen2 at a time,
                    Btoi(Txn.application_args[4]) == App.globalGet(degen2),
                ),
                Seq([
                    Assert(Btoi(Txn.application_args[3]) < Int(20000000)), 
                    Assert(Txn.assets[0] == App.globalGet(degen2)), # need to check because Txn.assets is the asset balance we check for
                    # check if we have enough to cover the stake
                    Assert(App.globalGet(total_stake) <= asset_balance.value() - Btoi(Txn.application_args[3])),  # total stake >= asset balance - request
                ])
            ),
            Assert(has_stake),  # ?TODO: add a mutable proposal_threshold (need > x stake to create proposal?)

            App.globalPut(proposal, Txn.application_args[1]),  # update proposal
            App.globalPut(proposal_type, Txn.application_args[2]),  # update proposal type to make execution easier
            App.globalPut(proposal_value, Btoi(Txn.application_args[3])),  # set amount receiver will get if vote passes
            App.globalPut(proposal_index, Btoi(Txn.application_args[4])),  # update proposal token to determine payout if needed
            App.globalPut(upvotes, Int(0)),  # reset upvotes in case proposal fails
            App.globalPut(dnvotes, Int(0)),  # reset dnvotes in case proposal fails
            App.globalPut(end_time, Global.latest_timestamp() + App.globalGet(duration)),  # start new vote from now to now + duration
            App.globalPut(receiver_address, Txn.accounts[1]),  # specify the receiver address (temporary)
            Approve()
        ]
    )

    # vote on proposal
    up_px = Seq([  # public
        # Safety Checks
        #Assert(Txn.sender() != App.globalGet(receiver_address)),
        Assert(has_stake),
        Assert(App.localGet(Txn.sender(), last_vote) < App.globalGet(end_time)-App.globalGet(duration)),  # last < start means you haven't voted yet
        Assert(Global.latest_timestamp() < App.globalGet(end_time)),  # must be before vote ends

        App.globalPut(upvotes, App.globalGet(upvotes) + App.localGet(Txn.sender(), stake)), # then increment vote by payment amount
        App.localPut(Txn.sender(), last_vote, Global.latest_timestamp()),  # update last with current time
        Approve(),
    ])

    dn_px = Seq([  # public
        # Safety Checks
        #Assert(Txn.sender() != App.globalGet(receiver_address)),
        Assert(has_stake),
        Assert(App.localGet(Txn.sender(), last_vote) < App.globalGet(end_time)-App.globalGet(duration)),  # last < start means you haven't voted yet
        Assert(Global.latest_timestamp() < App.globalGet(end_time)),  # must be before vote ends


        App.globalPut(dnvotes, App.globalGet(dnvotes) + App.localGet(Txn.sender(), stake)), # then increment vote by payment amount
        App.localPut(Txn.sender(), last_vote, Global.latest_timestamp()),  # update last with current time
        Approve(),
    ])

    # execute proposal
    change_proposal_fee = Seq(
        [
            Assert(App.globalGet(proposal_value)>(Int(2)*App.globalGet(proposal_fee)/Int(3))),  # can only change proposal_fee by less than 33% up or down
            Assert(App.globalGet(proposal_value)<(Int(4)*App.globalGet(proposal_fee)/Int(3))),
            Assert(App.globalGet(proposal_value)>Int(9)),  # proposal fee has to be > 9, otherwise it gets permanently stuck
            Assert(App.globalGet(proposal_value)<Int(20000000)),  # no more than 5% of the total supply
            App.globalPut(proposal_fee, App.globalGet(proposal_value)),
        ]
    )

    change_duration = Seq(
        [
            Assert(App.globalGet(proposal_value)>=min_duration), # will be necessary if we need to mass withdraw NFTs, 10k/10min = 69 days
            Assert(App.globalGet(proposal_index)>=Int(1800)), # will be necessary if we need to mass withdraw NFTs, 10k/10min = 69 days
            App.globalPut(duration, App.globalGet(proposal_value)),
            App.globalPut(cooldown, App.globalGet(proposal_index)),  # shouldn't really use the proposal_index this way, but allows you to change the cooldown cheaply
        ]
    )

    change_threshold = Seq(
        [
            Assert(
                And(  # this has to be 
                    App.globalGet(proposal_value)>(Int(2)*App.globalGet(threshold)/Int(3)),  # can only change threshold by less than 33% up or down
                    App.globalGet(proposal_value)<(Int(4)*App.globalGet(threshold)/Int(3)),
                    App.globalGet(proposal_value)>Int(4200000),  # must be greater than ~1% of total supply
                    App.globalGet(proposal_value)<Int(315000069)  # must be less than 75% of total supply
                )
            ),
            App.globalPut(threshold, App.globalGet(proposal_value)),
        ]
    )

    change_price = Seq(
        [
            Assert(
                And(
                    App.globalGet(proposal_value)>(Int(2)*App.globalGet(price)/Int(3)),  # can only change price by less than 33% up or down
                    App.globalGet(proposal_value)<(Int(4)*App.globalGet(price)/Int(3)),
                    App.globalGet(proposal_value)>Int(9),  # otherwise can't change the price
                    # price has no maximum, can always be lowered by vote, less important than bounding the min
                )
            ),
            App.globalPut(price, App.globalGet(proposal_value)),
        ]
    )

    clawback = Seq(  # by vote
        [
            Assert(Txn.assets[0] == App.globalGet(degen2)),
            Assert(Txn.accounts[1] == App.globalGet(receiver_address)),  # must pass in via accounts, invalid account err otherwise
            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: Txn.assets[0],
                TxnField.asset_amount: App.globalGet(proposal_value),
                TxnField.asset_receiver: Global.current_application_address(),  # only clawback to treasury
                TxnField.asset_sender: Txn.accounts[1],  # the receiver actually loses their tokens
            }),
            InnerTxnBuilder.Submit(),

            # to increment the number, they need to deposit into the contract
            # clawback will only remove tokens if they are unstaked
            # TODO: you need a separate method to slash someone's stake
            # App.localPut(Txn.sender(), stake, Int(0)),
        ]
    )

    slash_stake = Seq(  # by vote
        [
            Assert(Txn.accounts[1] == App.globalGet(receiver_address)),
            # can't slash more than 50% of someone's stake (can't be executed otherwise)
            Assert(App.globalGet(proposal_value) <= App.localGet(Txn.accounts[1], stake)/Int(2)),

            App.localPut(Txn.accounts[1], stake, App.localGet(Txn.accounts[1], stake) - App.globalGet(proposal_value)),  # reduce local stake
            App.globalPut(total_stake, App.globalGet(total_stake) - App.globalGet(proposal_value))  # reduce total, critical!   
        ]
    )

    pay_algo =  Seq(  # execute the pay proposal
        # close contract and send remainder balance back to creator
        # TODO: upgrade to new contract by transferring algo
        # if upvotes >= 280000046 (2*total_supply/3)

        [   
            # give proposal value
            Assert(Txn.accounts[1] == App.globalGet(receiver_address)),  # must pass in via accounts, invalid account err otherwise
            # if proposal value > 10% of balance, must check that upvotes >= 280000046 (2*total_supply/3) for extra security
            If(
                App.globalGet(proposal_value)>Int(1)*Balance(Global.current_application_address())/Int(10),
                Assert(App.globalGet(upvotes) >= Int(280000046))  # additional threshold to prevent algo withdraws
            ),

            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.Payment,
                TxnField.receiver: Txn.accounts[1],
                TxnField.amount: App.globalGet(proposal_value)
            }),
            InnerTxnBuilder.Submit(),
        ]
    )

    pay_token =  Seq(  # execute the pay proposal
        # close contract and send remainder balance back to creator

        [   
            # give proposal value
            Assert(Txn.assets[0] == App.globalGet(proposal_index)),  # must be the token they proposed
            Assert(Txn.accounts[1] == App.globalGet(receiver_address)),  # must pass in via accounts, invalid account err otherwise

            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: Txn.assets[0],
                TxnField.asset_receiver: Txn.accounts[1],
                TxnField.asset_amount: App.globalGet(proposal_value)
            }),
            InnerTxnBuilder.Submit(),
        ]
    )

    start_swap1 = Seq(
        [
            App.globalPut(swap_token1, App.globalGet(proposal_index)),
            App.globalPut(swap_ratio1, App.globalGet(proposal_value)),
            App.globalPut(end_swap1, Global.latest_timestamp()+week),
        ]
    )

    execute = Seq(
        [   
            Assert(is_proposal_over),
            If(
                did_proposal_pass,  # if this,
                Seq([  # then this
                        Cond(
                            [App.globalGet(proposal_type) == op_start_swap1, start_swap1],
                            [App.globalGet(proposal_type) == op_clawback, clawback],
                            [App.globalGet(proposal_type) == op_slash_stake, slash_stake],
                            [App.globalGet(proposal_type) == op_change_pf, change_proposal_fee],
                            [App.globalGet(proposal_type) == op_change_duration, change_duration],
                            [App.globalGet(proposal_type) == op_change_threshold, change_threshold],
                            [App.globalGet(proposal_type) == op_change_price, change_price],
                            [App.globalGet(proposal_type) == op_pay_algo, pay_algo],
                            [App.globalGet(proposal_type) == op_pay_token, pay_token],
                            [App.globalGet(proposal_type) != empty, Seq([reset])],  # evaluate as true
                        ), 
                        # pay the executor a small fee
                        InnerTxnBuilder.Begin(),
                        InnerTxnBuilder.SetFields({
                            TxnField.type_enum: TxnType.Payment,
                            TxnField.receiver: Txn.sender(),
                            TxnField.amount: Int(1000000)  # pay one algo back
                        }),
                        InnerTxnBuilder.Submit(),
                        
                ]),
            ),
            reset,  # always reset after execution
            Approve()
        ]
    )

    # public & protected
    token_opt_in = Seq(  # public, anyone can opt in as long as they pay
        [
            # users can opt into any token as long as they pay the fee
            # DAO benefits from more assets + fees, pretty much no harm I can think of from opting into too many assets
            Assert(Global.group_size() >= Int(2)),
            Assert(Gtxn[0].type_enum() == TxnType.Payment),  # essential, DAO benefits from deposits
            Assert(Gtxn[0].amount() >= Int(1000000)),  # cover txn fees plus more
            Assert(Gtxn[0].receiver() == Global.current_application_address()), 

            # must be a stake holder to opt into assets, must hold at least the proposal fee
            Assert(App.localGet(Txn.sender(), stake) >= App.globalGet(proposal_fee)),

            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.asset_receiver: Global.current_application_address(),
                TxnField.asset_amount: Int(0),
                TxnField.xfer_asset: Txn.assets[0], # Must be in the assets array sent as part of the application call
            }),
            InnerTxnBuilder.Submit(),
            Approve(),
        ]
    )

    token_opt_out = Seq(  # anyone can do it as long as the balance is 0
        [   
            # scratch vars
            asset_balance := AssetHolding.balance(Global.current_application_address(), Txn.assets[0]),

            # Safety checks
            Assert(asset_balance.value() == Int(0)),  # need to check that this is 0 otherwise they can rug the treasury    
            # anyone can opt out the contract out of assets with 0 balance

            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.asset_receiver: Global.current_application_address(),
                TxnField.asset_close_to: Txn.sender(),
                TxnField.asset_amount: Int(0),
                TxnField.xfer_asset: Txn.assets[0], # Must be in the assets array sent as part of the application call
            }),
            InnerTxnBuilder.Submit(),
            Approve(),
        ]
    )

    # local methods
    withdraw =  Seq(  # let people withdraw from theirlocal stake and then they can clear 
        [   
            Assert(App.globalGet(end_time) < Global.latest_timestamp()),  # only withdraw after voting phase, can leave during grace period
            If(  # if we're still in the grace period, assert sender isn't receiver. don't want someone to avoid getting stake slashed, but also don't want block receiver until next proposal clears out receiver address
                App.globalGet(end_time) + App.globalGet(cooldown) > Global.latest_timestamp(),  # still in grace period/vote
                Assert(Txn.sender() != App.globalGet(receiver_address)),  # receiver's stake is locked until execution is complete, otherwise slash_stake is impotent
            ),
            Assert(Gtxn[0].type_enum() == TxnType.Payment),  # essential, DAO benefits from deposits
            Assert(Gtxn[0].amount() >= Int(1000000)),  # cover txn fees plus prevents abuse
            Assert(Gtxn[0].receiver() == Global.current_application_address()), 

            Assert(Btoi(Txn.application_args[1]) <= App.localGet(Txn.sender(), stake)),
            Assert(Txn.assets[0] == App.globalGet(degen2)),
            
            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: Txn.assets[0],
                TxnField.asset_receiver: Txn.sender(),
                TxnField.asset_amount: Btoi(Txn.application_args[1]),
            }),
            InnerTxnBuilder.Submit(),
            App.localPut(Txn.sender(), stake, App.localGet(Txn.sender(), stake) - Btoi(Txn.application_args[1])),  # clear local stake
            
            # take from total stake
            App.globalPut(total_stake, App.globalGet(total_stake) - Btoi(Txn.application_args[1])),
            Approve(),
        ]
    )

    return program.event(
        init=on_creation,
        opt_in= contract_opt_in, 
        no_op=Cond(
            [Txn.application_args[0] == op_create_token, create_token],
            [Txn.application_args[0] == op_creator_token_opt_in, creator_token_opt_in],
            [Txn.application_args[0] == op_token_opt_in, token_opt_in],
            [Txn.application_args[0] == op_token_opt_out, token_opt_out],
            [Txn.application_args[0] == op_swap1, swap1],
            [Txn.application_args[0] == op_buy, buy],
            [Txn.application_args[0] == op_propose, propose],
            [Txn.application_args[0] == op_upvote, up_px],
            [Txn.application_args[0] == op_dnvote, dn_px],
            [Txn.application_args[0] == op_local_stake, local_stake],
            [Txn.application_args[0] == op_execute, execute],
            [Txn.application_args[0] == op_withdraw, withdraw]
        )
    )

def clear():
    return Approve()

if __name__ == "__main__":
    with open("degen2_approval.teal", "w") as f:
        compiled = compileTeal(approval(), mode=Mode.Application, version=6)
        f.write(compiled)

    with open("degen2_clear_state.teal", "w") as f:
        compiled = compileTeal(clear(), mode=Mode.Application, version=6)
        f.write(compiled)